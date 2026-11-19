"""Tests for AI image generation, independent record creation, Celery queuing, and flow node."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from bson.objectid import ObjectId

from archihub.api.aiservices import catalogue, chat, errors, images, providers, transport
from archihub.api.aiservices.dialects.anthropic import AnthropicDialect
from archihub.api.aiservices.dialects.google import GoogleDialect
from archihub.api.aiservices.dialects.ollama import OllamaDialect
from archihub.api.aiservices.dialects.openai_compat import OpenAICompatibleDialect
from archihub.plugins.framework import interop

#: Minimal valid 1x1 PNG bytes.
PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00"
    b"\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
PNG_B64 = base64.b64encode(PNG_1X1).decode("ascii")


class _Inserted:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeMongo:
    """The few collection operations the image path uses, in memory."""

    def __init__(self):
        self.collections: dict[str, list[dict]] = {}

    @staticmethod
    def _matches(document: dict, filters: dict) -> bool:
        for key, expected in (filters or {}).items():
            if isinstance(expected, dict) and "$ne" in expected:
                if document.get(key) == expected["$ne"]:
                    return False
            elif document.get(key) != expected:
                return False
        return True

    def get_record(self, collection, filters=None, fields=None):
        rows = self.collections.get(collection, [])
        return next((r for r in rows if self._matches(r, filters)), None)

    def insert_record(self, collection, record):
        document = {**record, "_id": record.get("_id") or ObjectId()}
        self.collections.setdefault(collection, []).append(document)
        return _Inserted(document["_id"])

    def update_record(self, collection, filters, update):
        for document in self.collections.get(collection, []):
            if self._matches(document, filters):
                document.update(update)


@pytest.fixture
def fake_mongo(monkeypatch):
    from archihub.api.records import storage

    from archihub.infra import mongo

    fake = FakeMongo()
    # Every module reads Mongo through this, so one fake serves them all.
    monkeypatch.setattr(mongo, "get_mongo", lambda: fake)
    monkeypatch.setattr(storage, "_mongo", lambda: fake)
    monkeypatch.setattr(storage, "_audit", lambda *args, **kwargs: None)
    return fake


@pytest.fixture
def tmp_settings(monkeypatch, tmp_path):
    """Settings whose original files live in a temporary folder."""
    from archihub.api.records import storage
    from archihub.core import files as filestore

    settings = storage.get_settings()
    monkeypatch.setattr(settings, "original_files_path", str(tmp_path), raising=False)
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    # The real sniffer needs libmagic; these tests are about the record.
    monkeypatch.setattr(filestore, "sniff_media_type", lambda path: "image/png")
    return settings


def test_openai_compat_discovers_image_generation_from_output_modalities():
    dialect = OpenAICompatibleDialect(api_key="k", base_url="https://example.test/v1")
    model = dialect._model({
        "id": "vendor/artist-1",
        "name": "Artist 1",
        "architecture": {
            "input_modalities": ["text"],
            "output_modalities": ["image"],
        },
    })
    assert model is not None
    assert "image_generation" in model.capabilities


def test_google_discovers_image_generation_from_predict_method():
    dialect = GoogleDialect(api_key="k")
    model = dialect._model({
        "name": "models/visual-gen-1",
        "displayName": "Visual Gen",
        "supportedGenerationMethods": ["predict"],
    })
    assert model is not None
    assert "image_generation" in model.capabilities


def test_openai_compat_generate_image_and_response_format_fallback(monkeypatch):
    calls: list[dict] = []

    def fake_request_json(method, url, *, headers=None, json_body=None, **_kwargs):
        calls.append({"method": method, "url": url, "body": dict(json_body or {})})
        if "response_format" in (json_body or {}):
            raise errors.ProviderError(
                errors.Reason.INVALID_REQUEST,
                "Unknown parameter: 'response_format'",
            )
        return {
            "model": "image-gen",
            "data": [{"b64_json": PNG_B64, "revised_prompt": "A bright sun"}],
        }

    monkeypatch.setattr(transport, "request_json", fake_request_json)
    dialect = OpenAICompatibleDialect(api_key="k", base_url="https://example.test/v1")
    result = dialect.generate_image("A sun", model="image-gen", size="1024x1024")

    assert len(calls) == 2
    assert "response_format" in calls[0]["body"]
    assert "response_format" not in calls[1]["body"]
    assert result.data == PNG_1X1
    assert result.mime_type == "image/png"
    assert result.revised_prompt == "A bright sun"


def test_google_generate_image_via_predict_and_generate_content(monkeypatch):
    def fake_request_json(method, url, *, headers=None, json_body=None, **_kwargs):
        if url.endswith(":predict"):
            return {
                "predictions": [
                    {"bytesBase64Encoded": PNG_B64, "mimeType": "image/png"}
                ]
            }
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Refined prompt"},
                            {"inlineData": {"mimeType": "image/png", "data": PNG_B64}},
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(transport, "request_json", fake_request_json)
    dialect = GoogleDialect(api_key="k")

    predict_res = dialect.generate_image(
        "A mountain", model="visual-gen", generation_methods=["predict"], aspect_ratio="16:9"
    )
    assert predict_res.data == PNG_1X1
    assert predict_res.mime_type == "image/png"

    content_res = dialect.generate_image(
        "A lake", model="multimodal-gen", generation_methods=["generateContent"]
    )
    assert content_res.data == PNG_1X1
    assert content_res.revised_prompt == "Refined prompt"


def test_anthropic_and_ollama_reject_image_generation():
    with pytest.raises(errors.ProviderError):
        AnthropicDialect(api_key="k").generate_image("A cat", model="m")
    with pytest.raises(errors.ProviderError):
        OllamaDialect(base_url="http://localhost:11434").generate_image("A cat", model="m")


def test_images_generate_creates_independent_record_and_queues_celery(
    fake_mongo, tmp_settings, monkeypatch
):
    from archihub.core import hooks as hooks_mod

    queued: list[str] = []
    record_hooks: list[dict] = []

    interop.reset()
    interop.provide(interop.RECORD_DERIVATIVES_TASK, "filesProcessing", lambda rid: queued.append(rid) or "task-123")
    hooks_mod.get_hook_handler().register("record_create", lambda doc: record_hooks.append(doc))

    class StubAdapter:
        def list_models(self):
            return []

        def generate_image(self, prompt, **options):
            from archihub.api.aiservices.dialects.base import ImageResult

            return ImageResult(data=PNG_1X1, mime_type="image/png", revised_prompt="Enhanced", model=options["model"])

    monkeypatch.setattr(catalogue, "build_adapter", lambda p: StubAdapter())

    provider = {"_id": ObjectId(), "name": "TestProvider", "dialect": "openai-compatible", "default_model": "artist"}
    res = images.generate(provider, "Draw a forest", user="alice", model="artist")

    assert res["record_id"]
    assert res["task_id"] == "task-123"
    assert queued == [res["record_id"]]
    assert len(record_hooks) == 1

    stored_doc = fake_mongo.get_record("records", {"_id": ObjectId(res["record_id"])})
    assert stored_doc is not None
    assert stored_doc["parent"] == []
    assert stored_doc["parents"] == []
    assert stored_doc["status"] == "uploaded"
    assert stored_doc["mime"] == "image/png"
    assert (Path(tmp_settings.original_files_path) / stored_doc["filepath"]).is_file()


def test_router_generate_image_returns_201_without_filepath(monkeypatch):
    from archihub.api.aiservices import router
    from archihub.core.security.jwt import CurrentUser

    provider = {"_id": ObjectId(), "name": "ImgEndpoint", "dialect": "openai-compatible", "enabled": True}
    monkeypatch.setattr(providers, "load", lambda provider_id: provider)
    monkeypatch.setattr(
        images,
        "generate",
        lambda provider, prompt, **kwargs: {
            "record_id": "rec-1",
            "record": {
                "id": "rec-1",
                "name": "gen.png",
                "mime": "image/png",
                "size": len(PNG_1X1),
                "hash": "abc",
                "status": "uploaded",
                "filepath": "/secret/server/path/gen.png",
            },
            "model": "artist",
            "revised_prompt": None,
            "deduplicated": False,
            "task_id": "task-1",
        },
    )

    resp = router.generate_image(
        str(provider["_id"]),
        {"prompt": "A bridge", "model": "artist"},
        CurrentUser(username="admin", claims={}),
    )

    assert resp.status_code == 201
    import json

    data = json.loads(resp.body)
    assert data["record_id"] == "rec-1"
    assert "filepath" not in data["record"]


@pytest.mark.parametrize(
    "provider,body,status",
    [
        (None, {"prompt": "x", "model": "m"}, 404),
        ({"_id": ObjectId(), "enabled": False}, {"prompt": "x", "model": "m"}, 403),
        ({"_id": ObjectId(), "enabled": True}, {"prompt": "  ", "model": "m"}, 400),
        ({"_id": ObjectId(), "enabled": True}, {"prompt": "x"}, 400),
    ],
    ids=["missing provider", "disabled provider", "blank prompt", "no model"],
)
def test_router_generate_image_refuses_before_calling_the_provider(monkeypatch, provider, body, status):
    from archihub.api.aiservices import router
    from archihub.core.security.jwt import CurrentUser

    monkeypatch.setattr(providers, "load", lambda provider_id: provider)
    monkeypatch.setattr(images, "generate", lambda *a, **k: pytest.fail("the provider was called"))

    resp = router.generate_image("p", body, CurrentUser(username="admin", claims={}))

    assert resp.status_code == status


def test_flow_image_generation_node_save_modes(fake_mongo, tmp_settings, monkeypatch):
    # The node ships with flowsManager, its own repository; without it there is
    # nothing to test.
    node_module = pytest.importorskip("archihub.plugins.flowsManager.utils.nodes.image_generation")
    from archihub.plugins.flowsManager import ai as flows_ai

    ImageGenerationNode = node_module.ImageGenerationNode

    interop.reset()
    interop.provide(interop.RECORD_DERIVATIVES_TASK, "filesProcessing", lambda rid: "celery-task-99")

    class StubAdapter:
        def list_models(self):
            return []

        def generate_image(self, prompt, **options):
            from archihub.api.aiservices.dialects.base import ImageResult

            return ImageResult(data=PNG_1X1, mime_type="image/png", model=options["model"])

    fake_mongo.insert_record(
        "users",
        {"username": "editor1", "roles": ["admin"], "status": "active"},
    )
    fake_mongo.insert_record(
        "post_types",
        {
            "slug": "documento",
            "name": "Documento",
            "metadata": {"fields": [{"destiny": "title", "type": "text", "required": False}]},
        },
    )
    existing_res_id = str(
        fake_mongo.insert_record(
            "resources",
            {
                "post_type": "documento",
                "metadata": {"title": "Existente"},
                "status": "draft",
                "createdBy": "editor1",
                "filesObj": [],
            },
        ).inserted_id
    )

    provider_doc = {
        "_id": ObjectId(),
        "name": "ImgProvider",
        "dialect": "openai-compatible",
        "default_model": "artist",
        "enabled": True,
    }
    monkeypatch.setattr(flows_ai, "resolve_provider", lambda name: provider_doc)
    monkeypatch.setattr(catalogue, "build_adapter", lambda p: StubAdapter())

    # 1) record_only
    node_only = ImageGenerationNode(
        {
            "id": "n1",
            "settings": {
                "provider": "ImgProvider",
                "selected_model": "artist",
                "prompt_template": "Solo {{prompt}}",
                "save_mode": "record_only",
            },
        },
        {},
        mongodb_instance=fake_mongo,
        current_user="editor1",
    )
    out_only = node_only.execute({"prompt": "uno"})
    assert out_only["record_id"]
    assert out_only["resource_id"] == ""
    assert out_only["task_id"] == "celery-task-99"

    # 2) existing_resource (using different bytes so it's not deduplicated with #1)
    monkeypatch.setattr(
        StubAdapter,
        "generate_image",
        lambda self, prompt, **options: __import__(
            "archihub.api.aiservices.dialects.base", fromlist=["ImageResult"]
        ).ImageResult(data=PNG_1X1 + b"\x00", mime_type="image/png", model=options["model"]),
    )
    node_existing = ImageGenerationNode(
        {
            "id": "n2",
            "settings": {
                "provider": "ImgProvider",
                "selected_model": "artist",
                "prompt_template": "{{prompt}}",
                "save_mode": "existing_resource",
                "file_tag": "cover",
            },
        },
        {},
        mongodb_instance=fake_mongo,
        current_user="editor1",
    )
    out_existing = node_existing.execute({"prompt": "dos", "resource_id": existing_res_id})
    assert out_existing["resource_id"] == existing_res_id
    updated_res = fake_mongo.get_record("resources", {"_id": ObjectId(existing_res_id)})
    assert any(f["id"] == out_existing["record_id"] and f["tag"] == "cover" for f in updated_res["filesObj"])

    # 3) new_resource
    monkeypatch.setattr(
        StubAdapter,
        "generate_image",
        lambda self, prompt, **options: __import__(
            "archihub.api.aiservices.dialects.base", fromlist=["ImageResult"]
        ).ImageResult(data=PNG_1X1 + b"\x01", mime_type="image/png", model=options["model"]),
    )
    node_new = ImageGenerationNode(
        {
            "id": "n3",
            "settings": {
                "provider": "ImgProvider",
                "selected_model": "artist",
                "prompt_template": "{{prompt}}",
                "save_mode": "new_resource",
                "new_resource_post_type": "documento",
                "new_resource_title": "Recurso generado: {{prompt}}",
            },
        },
        {},
        mongodb_instance=fake_mongo,
        current_user="editor1",
    )
    out_new = node_new.execute({"prompt": "tres"})
    assert out_new["resource_id"]
    new_res = fake_mongo.get_record("resources", {"_id": ObjectId(out_new["resource_id"])})
    assert new_res is not None
    assert new_res["metadata"]["title"] == "Recurso generado: tres"
    assert any(f["id"] == out_new["record_id"] for f in new_res["filesObj"])

