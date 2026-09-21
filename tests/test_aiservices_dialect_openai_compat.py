"""The OpenAI-compatible dialect's own request shaping.

`document_url` is this backend's internal convention for "a whole file", used
identically across every dialect's message-building. Every dialect except this
one happens to already speak it as-is for `image_url`/`text`; `document_url`
has no native counterpart here, so it is the one part type this dialect must
translate before a request goes out.
"""

from __future__ import annotations

from archihub.api.aiservices.dialects.openai_compat import OpenAICompatibleDialect

DIALECT = OpenAICompatibleDialect(api_key="test-key")


def test_a_document_part_becomes_a_native_file_part():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "document_url", "document_url": {"url": "data:application/pdf;base64,QUJD", "name": "report.pdf"}},
                {"type": "text", "text": "Summarise this"},
            ],
        }
    ]

    body = DIALECT._body(messages, {"model": "gpt-5.6-terra"}, stream=False)
    parts = body["messages"][0]["content"]

    assert parts[0] == {
        "type": "file",
        "file": {"filename": "report.pdf", "file_data": "data:application/pdf;base64,QUJD"},
    }
    assert parts[1] == {"type": "text", "text": "Summarise this"}


def test_a_document_part_without_a_name_gets_a_fallback_filename():
    messages = [
        {"role": "user", "content": [{"type": "document_url", "document_url": {"url": "data:application/pdf;base64,QUJD"}}]}
    ]

    body = DIALECT._body(messages, {"model": "gpt-5.6-terra"}, stream=False)

    assert body["messages"][0]["content"][0]["file"]["filename"] == "document.pdf"


def test_messages_with_no_document_part_are_left_untouched():
    """Identity, not just equal content - a plain string message must not be
    rewritten into a content-part list it never had."""
    messages = [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}]},
    ]

    body = DIALECT._body(messages, {"model": "gpt-5.6-terra"}, stream=False)

    assert body["messages"][0] is messages[0]
    assert body["messages"][1] is messages[1]
