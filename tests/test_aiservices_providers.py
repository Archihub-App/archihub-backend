"""The multi-provider layer: dialects, discovery, classification, chat.

The dialects are exercised against a **real HTTP server** rather than a mocked
client, so the tests cover the wire format — request bodies, streaming framing,
error shapes — and not just our own function calls. That matters here: every
defect this rewrite fixes was a wire-level assumption that went unchecked.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from archihub.api.aiservices import catalogue, chat, errors, streaming, transport
from archihub.api.aiservices.dialects import DIALECTS, get_dialect
from archihub.api.aiservices.dialects.anthropic import AnthropicDialect
from archihub.api.aiservices.dialects.base import ModelInfo, normalise_capabilities
from archihub.api.aiservices.dialects.google import GoogleDialect
from archihub.api.aiservices.dialects.ollama import OllamaDialect
from archihub.api.aiservices.dialects.openai_compat import OpenAICompatibleDialect


# ---------------------------------------------------------------------------
# A stub provider, speaking whatever we tell it to
# ---------------------------------------------------------------------------


class Stub:
    """Routes, recorded requests, and canned responses."""

    def __init__(self):
        self.routes: dict[str, tuple[int, object]] = {}
        self.requests: list[dict] = []
        self.raw: dict[str, tuple[int, str, str]] = {}

    def json(self, path, body, status=200):
        self.routes[path] = (status, body)

    def text(self, path, body, status=200, content_type="text/event-stream"):
        self.raw[path] = (status, body, content_type)


class _Handler(BaseHTTPRequestHandler):
    stub: Stub = None  # set per server

    def log_message(self, *args):  # silence
        pass

    def _respond(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        self.stub.requests.append(
            {
                "path": path,
                "query": self.path,
                "method": self.command,
                "headers": dict(self.headers),
                "body": json.loads(body) if body else None,
            }
        )

        if path in self.stub.raw:
            status, payload, content_type = self.stub.raw[path]
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(payload.encode())
            return

        status, payload = self.stub.routes.get(path, (404, {"error": {"message": "no route"}}))
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    do_GET = _respond
    do_POST = _respond


@pytest.fixture
def stub():
    server_stub = Stub()
    handler = type("H", (_Handler,), {"stub": server_stub})
    server = HTTPServer(("127.0.0.1", 0), handler)
    # A short poll interval: shutdown() waits for one, and the default of
    # half a second turns 30 tests into 15 seconds of teardown.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()

    server_stub.base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield server_stub
    finally:
        server.shutdown()
        server.server_close()
        transport.reset_client()


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    """No real sleeping between retries."""
    monkeypatch.setattr(transport.time, "sleep", lambda seconds: None)


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


def test_the_http_status_is_trusted_before_any_message():
    assert errors.classify(status_code=429) is errors.Reason.RATE_LIMITED
    assert errors.classify(status_code=401) is errors.Reason.AUTH
    assert errors.classify(status_code=404) is errors.Reason.MODEL_NOT_FOUND
    assert errors.classify(status_code=503) is errors.Reason.UNAVAILABLE


def test_a_provider_code_beats_an_overloaded_status():
    """A 400 carrying `context_length_exceeded` is not a generic bad request."""
    reason = errors.classify(status_code=400, provider_code="context_length_exceeded")

    assert reason is errors.Reason.CONTEXT_LENGTH


def test_an_unmapped_5xx_is_treated_as_unavailable():
    assert errors.classify(status_code=522) is errors.Reason.UNAVAILABLE


def test_the_rate_limit_and_context_phrases_do_not_collide():
    """'token limit' appears in both; the legacy code needed a special case."""
    assert errors.classify(message="You exceeded your rate limit, 3 per min") is errors.Reason.RATE_LIMITED
    assert errors.classify(message="maximum context length is 8192 tokens") is errors.Reason.CONTEXT_LENGTH


def test_an_unrecognised_failure_is_unknown_and_not_retried():
    reason = errors.classify(message="the flux capacitor is misaligned")

    assert reason is errors.Reason.UNKNOWN
    assert reason not in errors.RETRYABLE


@pytest.mark.parametrize(
    "body,expected_code",
    [
        ({"error": {"message": "no", "code": "rate_limit_exceeded"}}, "rate_limit_exceeded"),
        ({"error": {"message": "no", "type": "invalid_request_error"}}, "invalid_request_error"),
        ({"error": "plain string"}, None),
        ("just text", None),
    ],
)
def test_error_bodies_are_unpacked_whatever_shape_they_arrive_in(body, expected_code):
    error = errors.from_response(400, body)

    assert error.provider_code == expected_code
    assert isinstance(str(error), str) and str(error)


def test_only_reasons_a_retry_can_fix_are_retryable():
    assert errors.Reason.RATE_LIMITED in errors.RETRYABLE
    assert errors.Reason.UNAVAILABLE in errors.RETRYABLE
    assert errors.Reason.AUTH not in errors.RETRYABLE
    assert errors.Reason.INVALID_REQUEST not in errors.RETRYABLE
    assert errors.Reason.CONTEXT_LENGTH not in errors.RETRYABLE


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def test_a_rate_limited_request_is_retried_then_succeeds(stub, monkeypatch):
    calls = {"n": 0}
    original = transport.get_client().request

    def flaky(method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            stub.json("/models", {"error": {"code": "rate_limit_exceeded"}}, status=429)
        else:
            stub.json("/models", {"data": [{"id": "m"}]})
        return original(method, url, **kwargs)

    monkeypatch.setattr(transport.get_client(), "request", flaky)
    dialect = OpenAICompatibleDialect(base_url=stub.base_url, api_key="k")

    assert [m.id for m in dialect.list_models()] == ["m"]
    assert calls["n"] == 2


def test_an_auth_failure_is_not_retried(stub):
    stub.json("/models", {"error": {"message": "bad key", "code": "invalid_api_key"}}, status=401)
    dialect = OpenAICompatibleDialect(base_url=stub.base_url, api_key="k")

    with pytest.raises(errors.ProviderError) as exc:
        dialect.list_models()

    assert exc.value.reason is errors.Reason.AUTH
    assert len(stub.requests) == 1


def test_a_non_json_body_is_a_clear_failure_not_a_crash(stub):
    stub.text("/models", "<html>gateway error</html>", status=200, content_type="text/html")
    dialect = OpenAICompatibleDialect(base_url=stub.base_url)

    with pytest.raises(errors.ProviderError):
        dialect.list_models()


def test_the_credential_travels_as_a_header_not_a_query_parameter(stub):
    stub.json("/models", {"data": []})
    OpenAICompatibleDialect(base_url=stub.base_url, api_key="secret-key").list_models()

    request = stub.requests[0]
    assert request["headers"]["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in request["query"]


# ---------------------------------------------------------------------------
# Discovery reads what the provider says, and nothing else
# ---------------------------------------------------------------------------


def test_openai_compatible_discovery_reports_only_what_was_declared(stub):
    """OpenAI's /models returns bare ids; nothing is invented to fill the gap."""
    stub.json("/models", {"data": [{"id": "some-model", "object": "model"}]})

    model = OpenAICompatibleDialect(base_url=stub.base_url).list_models()[0]

    assert model.id == "some-model"
    assert model.context_length is None
    assert model.capabilities == ()


def test_richer_providers_have_their_metadata_read(stub):
    """OpenRouter-shaped entries carry context length and input modalities."""
    stub.json(
        "/models",
        {
            "data": [
                {
                    "id": "vendor/model",
                    "name": "A Model",
                    "context_length": 200000,
                    "architecture": {"input_modalities": ["text", "image"]},
                    "top_provider": {"max_completion_tokens": 8192},
                }
            ]
        },
    )

    model = OpenAICompatibleDialect(base_url=stub.base_url).list_models()[0]

    assert model.context_length == 200000
    assert model.max_output_tokens == 8192
    assert "image" in model.capabilities


def test_a_bare_list_response_is_understood(stub):
    """Some self-hosted servers answer with `models` rather than `data`."""
    stub.json("/models", {"models": [{"id": "local-model"}]})

    assert OpenAICompatibleDialect(base_url=stub.base_url).list_models()[0].id == "local-model"


def test_ollama_capabilities_come_from_the_daemon_not_from_the_name(stub):
    """The legacy code guessed vision support from substrings of the model name.

    A model whose name contains none of those strings was invisible as a vision
    model; one that coincidentally did was wrongly advertised as one.
    """
    stub.json("/api/tags", {"models": [{"model": "something-opaque:latest", "details": {"family": "x"}}]})
    stub.json(
        "/api/show",
        {"capabilities": ["completion", "vision"], "model_info": {"whatever.context_length": 32768}},
    )

    model = OllamaDialect(base_url=stub.base_url).list_models()[0]

    assert "image" in model.capabilities
    assert model.context_length == 32768


def test_an_ollama_daemon_too_old_to_report_capabilities_claims_none(stub):
    stub.json("/api/tags", {"models": [{"model": "old:latest"}]})
    stub.json("/api/show", {"error": "not found"}, status=404)

    model = OllamaDialect(base_url=stub.base_url).list_models()[0]

    assert model.capabilities == ()
    assert model.context_length is None


def test_google_discovery_reads_the_real_token_limits(stub):
    """These numbers were hardcoded per model name in the legacy module."""
    stub.json(
        "/models",
        {
            "models": [
                {
                    "name": "models/some-gemini",
                    "displayName": "Some Gemini",
                    "inputTokenLimit": 1048576,
                    "outputTokenLimit": 8192,
                    "supportedGenerationMethods": ["generateContent"],
                }
            ]
        },
    )

    model = GoogleDialect(base_url=stub.base_url, api_key="k").list_models()[0]

    assert model.id == "some-gemini"
    assert model.context_length == 1048576
    assert model.max_output_tokens == 8192
    assert "chat" in model.capabilities


def test_google_models_that_cannot_chat_are_left_out(stub):
    stub.json(
        "/models",
        {"models": [{"name": "models/tuner", "supportedGenerationMethods": ["createTunedModel"]}]},
    )

    assert GoogleDialect(base_url=stub.base_url).list_models() == []


def test_anthropic_discovery_reads_the_listing(stub):
    stub.json(
        "/models",
        {"data": [{"id": "claude-x", "display_name": "Claude X", "context_window": 200000}]},
    )

    model = AnthropicDialect(base_url=stub.base_url, api_key="k").list_models()[0]

    assert (model.id, model.name, model.context_length) == ("claude-x", "Claude X", 200000)


# ---------------------------------------------------------------------------
# Capability vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        (["vision"], ("image",)),
        (["multimodal"], ("image",)),
        (["function_calling"], ("tools",)),
        (["completion", "text"], ("chat",)),
        (["thinking"], ("reasoning",)),
        (["nonsense"], ()),
        ([None, 42], ()),
        (None, ()),
    ],
)
def test_provider_vocabularies_map_onto_one_closed_set(given, expected):
    assert normalise_capabilities(given) == expected


# ---------------------------------------------------------------------------
# Calling
# ---------------------------------------------------------------------------


def _completion(text="hello"):
    return {
        "model": "m",
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }


def test_a_completion_is_normalised(stub):
    stub.json("/chat/completions", _completion())
    dialect = OpenAICompatibleDialect(base_url=stub.base_url)

    result = dialect.chat([{"role": "user", "content": "hi"}], model="m")

    assert result.content == "hello"
    assert result.finish_reason == "stop"
    assert result.usage == {"prompt": 3, "completion": 2, "total": 5}


def test_content_returned_as_typed_parts_is_flattened(stub):
    """Reasoning models return a part list where older models return a string."""
    stub.json(
        "/chat/completions",
        {"choices": [{"message": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}]},
    )

    result = OpenAICompatibleDialect(base_url=stub.base_url).chat([], model="m")

    assert result.content == "ab"


def test_the_token_ceiling_field_is_the_callers_choice_not_a_name_prefix(stub):
    """The legacy code chose between `max_tokens` and `max_completion_tokens`
    by testing whether the model id started with gpt-5/o1/o3/o4."""
    stub.json("/chat/completions", _completion())
    dialect = OpenAICompatibleDialect(base_url=stub.base_url)

    dialect.chat([], model="anything", max_tokens=64, max_tokens_field="max_completion_tokens")

    assert stub.requests[-1]["body"]["max_completion_tokens"] == 64
    assert "max_tokens" not in stub.requests[-1]["body"]


def test_streaming_yields_deltas(stub):
    stub.text(
        "/chat/completions",
        'data: {"choices":[{"delta":{"content":"He"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n",
    )
    dialect = OpenAICompatibleDialect(base_url=stub.base_url)

    chunks = list(dialect.stream([], model="m"))

    assert "".join(c.delta for c in chunks) == "Hello"
    assert chunks[-1].finish_reason == "stop"


def test_a_stream_that_fails_before_any_bytes_raises_the_classified_error(stub):
    stub.json("/chat/completions", {"error": {"code": "invalid_api_key"}}, status=401)
    dialect = OpenAICompatibleDialect(base_url=stub.base_url)

    with pytest.raises(errors.ProviderError) as exc:
        list(dialect.stream([], model="m"))

    assert exc.value.reason is errors.Reason.AUTH


def test_ollama_streams_newline_delimited_json_not_sse(stub):
    stub.text(
        "/api/chat",
        '{"message":{"content":"one "},"done":false}\n'
        '{"message":{"content":"two"},"done":true,"done_reason":"stop",'
        '"prompt_eval_count":5,"eval_count":2}\n',
        content_type="application/x-ndjson",
    )

    chunks = list(OllamaDialect(base_url=stub.base_url).stream([], model="m"))

    assert "".join(c.delta for c in chunks) == "one two"
    assert chunks[-1].usage == {"prompt": 5, "completion": 2, "total": 7}


def test_ollama_takes_images_as_a_separate_list(stub):
    """Its native API does not accept inline content parts."""
    stub.json("/api/chat", {"message": {"content": "ok"}})
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
        ],
    }

    OllamaDialect(base_url=stub.base_url).chat([message], model="m")

    sent = stub.requests[-1]["body"]["messages"][0]
    assert sent["content"] == "what is this"
    assert sent["images"] == ["AAAA"]


def test_google_puts_the_system_prompt_in_its_own_field(stub):
    """The legacy converter turned it into an ordinary user turn."""
    stub.json("/models/m:generateContent", {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    messages = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]

    GoogleDialect(base_url=stub.base_url, api_key="k").chat(messages, model="m")

    body = stub.requests[-1]["body"]
    assert body["systemInstruction"]["parts"] == [{"text": "be brief"}]
    assert [c["role"] for c in body["contents"]] == ["user"]


def test_anthropic_lifts_the_system_prompt_and_requires_max_tokens(stub):
    stub.json("/messages", {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"})
    messages = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]

    result = AnthropicDialect(base_url=stub.base_url, api_key="k").chat(messages, model="claude")

    body = stub.requests[-1]["body"]
    assert body["system"] == [{"type": "text", "text": "be brief"}]
    assert body["max_tokens"] > 0
    assert result.content == "ok"


def test_anthropic_streams_typed_events(stub):
    stub.text(
        "/messages",
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n\n'
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        '"usage":{"input_tokens":4,"output_tokens":1}}\n\n',
    )

    chunks = list(AnthropicDialect(base_url=stub.base_url, api_key="k").stream([], model="c"))

    assert "".join(c.delta for c in chunks) == "Hi"
    assert chunks[-1].usage["total"] == 5


# ---------------------------------------------------------------------------
# Catalogue behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
def no_overrides(monkeypatch):
    monkeypatch.setattr(catalogue, "load_overrides", lambda provider_id: {})


def _provider(stub, dialect="openai-compatible"):
    return {"id": "p1", "dialect": dialect, "base_url": stub.base_url, "key": None}


def test_a_discovery_failure_is_reported_not_replaced_with_a_guess(stub, no_overrides, monkeypatch):
    """The legacy code returned a hardcoded model list when discovery failed.

    A provider with an expired key therefore presented a normal-looking
    catalogue of models that could not be called.
    """
    monkeypatch.setattr(catalogue, "decrypt_key", lambda key: None, raising=False)
    monkeypatch.setattr("archihub.api.aiservices.providers.decrypt_key", lambda key: None)
    catalogue.clear_cache()
    stub.json("/models", {"error": {"code": "invalid_api_key"}}, status=401)

    result = catalogue.for_provider(_provider(stub))

    assert result.models == []
    assert result.error
    assert result.reason == "auth"


def test_a_transient_failure_keeps_the_previous_catalogue_and_says_it_is_stale(
    stub, no_overrides, monkeypatch
):
    monkeypatch.setattr("archihub.api.aiservices.providers.decrypt_key", lambda key: None)
    catalogue.clear_cache()
    stub.json("/models", {"data": [{"id": "m"}]})
    catalogue.for_provider(_provider(stub))

    stub.json("/models", {"error": {"message": "down"}}, status=503)
    result = catalogue.for_provider(_provider(stub), refresh=True)

    assert [m.id for m in result.models] == ["m"]
    assert result.stale is True
    assert result.reason == "unavailable"


def test_operator_overrides_win_only_where_they_are_set(monkeypatch):
    model = ModelInfo(id="m", name="M", context_length=1000, capabilities=("chat",))

    merged = model.merged_with({"capabilities": ["chat", "image"]})

    assert merged.capabilities == ("chat", "image")
    assert merged.context_length == 1000


def test_an_empty_override_changes_nothing():
    model = ModelInfo(id="m", name="M")

    assert model.merged_with({}) is model


# ---------------------------------------------------------------------------
# Chat behaviour
# ---------------------------------------------------------------------------


def test_a_request_needing_images_is_refused_by_a_model_known_to_lack_them():
    model = ModelInfo(id="m", name="M", capabilities=("chat",))
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:,"}}]}]

    with pytest.raises(chat.CapabilityError):
        chat.check_capabilities(model, messages, {})


def test_a_model_declaring_nothing_is_never_blocked():
    """OpenAI's /models says nothing; refusing on silence would block everything."""
    model = ModelInfo(id="m", name="M", capabilities=())
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:,"}}]}]

    chat.check_capabilities(model, messages, {})


def test_tools_are_a_required_capability_too():
    model = ModelInfo(id="m", name="M", capabilities=("chat",))

    with pytest.raises(chat.CapabilityError):
        chat.check_capabilities(model, [], {"tools": [{"type": "function"}]})


def test_compression_keeps_the_system_prompt_and_the_recent_turns():
    messages = [{"role": "system", "content": "rules"}] + [
        {"role": "user", "content": "x" * 1000} for _ in range(10)
    ]

    compressed = chat.compress(messages, keep_tail=2)

    assert compressed[0] == messages[0]
    assert compressed[-2:] == messages[-2:]
    assert len(compressed[3]["content"]) < 1000


def test_compression_leaves_a_short_conversation_alone():
    messages = [{"role": "user", "content": "hi"}]

    assert chat.compress(messages, keep_tail=8) == messages


def test_an_over_long_conversation_is_shrunk_and_retried(stub, monkeypatch):
    monkeypatch.setattr("archihub.api.aiservices.providers.decrypt_key", lambda key: None)
    monkeypatch.setattr(catalogue, "find_model", lambda provider, model_id: None)
    catalogue.clear_cache()

    attempts = {"n": 0}
    original = OpenAICompatibleDialect.chat

    def failing_first(self, messages, **options):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise errors.ProviderError(errors.Reason.CONTEXT_LENGTH, "too long")
        return original(self, messages, **options)

    monkeypatch.setattr(OpenAICompatibleDialect, "chat", failing_first)
    stub.json("/chat/completions", _completion("shorter"))

    messages = [{"role": "user", "content": "x" * 2000} for _ in range(20)]
    result = chat.complete(_provider(stub), messages, model="m")

    assert result["content"] == "shorter"
    assert attempts["n"] == 2


def test_a_failure_that_shrinking_cannot_fix_is_raised_immediately(stub, monkeypatch):
    monkeypatch.setattr("archihub.api.aiservices.providers.decrypt_key", lambda key: None)
    monkeypatch.setattr(catalogue, "find_model", lambda provider, model_id: None)
    catalogue.clear_cache()
    stub.json("/chat/completions", {"error": {"code": "invalid_api_key"}}, status=401)

    with pytest.raises(errors.ProviderError) as exc:
        chat.complete(_provider(stub), [{"role": "user", "content": "hi"}], model="m")

    assert exc.value.reason is errors.Reason.AUTH
    assert len(stub.requests) == 1


def test_tool_arguments_survive_either_shape_and_malformed_json():
    assert chat.parse_tool_arguments({"function": {"arguments": '{"a": 1}'}}) == {"a": 1}
    assert chat.parse_tool_arguments({"function": {"arguments": {"a": 1}}}) == {"a": 1}
    assert chat.parse_tool_arguments({"function": {"arguments": "{oops"}}) == {}
    assert chat.parse_tool_arguments({}) == {}


# ---------------------------------------------------------------------------
# SSE framing
# ---------------------------------------------------------------------------


def test_frames_are_separated_by_real_newlines():
    """The legacy code emitted a literal backslash-n, so no standard SSE client
    could read the stream."""
    rendered = streaming.frame({"delta": "hi"})

    assert rendered.endswith("\n\n")
    assert "\\n" not in rendered


def test_accented_text_is_not_escaped():
    assert "ñ" in streaming.frame({"delta": "mañana"})


def test_a_stream_ends_with_the_done_terminator():
    class Chunk:
        delta = "hi"
        tool_calls = None
        finish_reason = None
        usage = None

    rendered = list(streaming.event_stream(iter([Chunk()])))

    assert rendered[-1] == "data: [DONE]\n\n"


def test_a_mid_stream_failure_is_delivered_as_an_event_not_a_dropped_socket():
    """The status line is long gone by then; a closed socket is unreadable."""

    def failing():
        raise errors.ProviderError(errors.Reason.RATE_LIMITED, "slow down")
        yield  # pragma: no cover

    rendered = "".join(streaming.event_stream(failing()))

    assert "event: error" in rendered
    assert "rate_limited" in rendered
    assert rendered.endswith("data: [DONE]\n\n")


def test_an_unexpected_failure_does_not_leak_its_detail():
    def failing():
        raise RuntimeError("/srv/archihub/secret/path exploded")
        yield  # pragma: no cover

    rendered = "".join(streaming.event_stream(failing()))

    assert "/srv" not in rendered
    assert "unknown" in rendered


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def test_every_dialect_is_registered_under_its_own_name():
    for name, adapter in DIALECTS.items():
        assert adapter.name == name


def test_an_unknown_dialect_names_what_is_available():
    with pytest.raises(KeyError) as exc:
        get_dialect("telepathy")

    assert "openai-compatible" in str(exc.value)


def test_no_module_in_this_package_contains_a_model_name_table():
    """The point of the rewrite: model facts come from providers, not source.

    A guard rather than a nicety - the legacy module accumulated three such
    tables (`_OPENAI_META`, `_GOOGLE_META`, the Ollama family substrings), and
    each was wrong the day a vendor shipped anything.

    Parsed rather than grepped, so that *discussing* the old hardcoded lists in
    a docstring is fine and only a real string literal in executable code fails.
    """
    import ast
    import pathlib
    import re

    package = pathlib.Path(__file__).resolve().parents[1] / "archihub" / "api" / "aiservices"
    looks_like_a_model = re.compile(
        r"^(gpt-[0-9]|o[134]-|claude-[0-9]|gemini-[0-9]|gemma-?[0-9]|llama-?[0-9]|mistral-|qwen[0-9])",
        re.I,
    )

    offenders = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            ast.walk(node).__next__() and node.body[0].value
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }

        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node in docstrings:
                continue
            if looks_like_a_model.match(node.value.strip()):
                offenders.append(f"{path.name}:{node.lineno} -> {node.value!r}")

    assert offenders == [], f"model names hardcoded at: {offenders}"


def test_recording_an_override_returns_something_json_serialisable(monkeypatch):
    """It writes first and returns second.

    Returning a raw datetime made the response a 500 *after* the write had
    already landed - the caller is told it failed when it did not. Found by
    exercising the route live, not by these tests, which is why it is now one.
    """
    import json

    written = {}
    monkeypatch.setattr(
        catalogue, "_mongo",
        lambda: type("M", (), {"upsert_record": lambda self, c, f, d: written.update(d)})(),
    )
    monkeypatch.setattr(catalogue, "clear_cache", lambda provider_id=None: None)

    result = catalogue.set_override("p", "m", {"capabilities": ["chat"]}, "alice")

    json.dumps(result)
    assert result["capabilities"] == ["chat"]
    assert isinstance(result["updatedAt"], str)
