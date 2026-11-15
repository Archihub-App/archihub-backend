"""Image generation over the configured AI provider dialects.

Each generated image is stored immediately as an independent ``record`` in
``records.storage`` and queued for derivative processing via the configured
Celery tasks (``filesProcessing``).
"""

from __future__ import annotations

import io
import logging
import uuid
from typing import Any

from archihub.api.aiservices import catalogue, chat, providers
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

_MIME_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


def check_image_capabilities(model) -> None:
    """Refuse when the model explicitly declares capabilities without image generation.

    Follows the same positive-knowledge rule as ``chat.check_capabilities``:
    when a provider reports no capabilities for a model, it is not blocked.
    """
    if model is None or not model.capabilities:
        return

    declared = set(model.capabilities)
    if "image_generation" in declared:
        return

    raise chat.CapabilityError(
        _(
            'The model "{model}" does not support: {missing}',
            model=model.id,
            missing="image_generation",
        )
    )


def _find_model(provider: dict, model_id: str):
    """Best-effort catalogue lookup for ``model_id``; returns ``None`` on failure."""
    try:
        result = catalogue.for_provider(provider)
    except Exception:
        logger.debug("Could not inspect catalogue for provider %s", provider.get("name"), exc_info=True)
        return None
    for entry in result.models:
        if entry.id == model_id:
            return entry
    return None


def _filename_for(filename: str | None, mime_type: str) -> str:
    ext = _MIME_EXTENSIONS.get((mime_type or "").lower(), "png")
    if filename:
        stem = filename.rsplit(".", 1)[0].strip() if "." in filename else filename.strip()
        if stem:
            return f"{stem}.{ext}"
    return f"generated-{uuid.uuid4().hex[:12]}.{ext}"


def generate(
    provider: dict,
    prompt: str,
    *,
    user: str | None = None,
    model: str | None = None,
    filename: str | None = None,
    **options: Any,
) -> dict:
    """Generate an image, persist it as an independent record, and queue Celery processing."""
    from archihub.api.records import storage
    from archihub.plugins.framework import interop

    clean_prompt = (prompt or "").strip()
    if not clean_prompt:
        raise ValueError(_("Prompt is required"))

    model_id = model or provider.get("default_model")
    if not model_id:
        raise ValueError(_("A model is required"))

    model_info = _find_model(provider, model_id)
    check_image_capabilities(model_info)

    call_options = {k: v for k, v in options.items() if v is not None}
    call_options["model"] = model_id
    if model_info is not None and isinstance(model_info.metadata, dict):
        methods = model_info.metadata.get("methods")
        if methods:
            call_options.setdefault("generation_methods", methods)

    adapter = providers.adapter_for(provider)
    image = adapter.generate_image(clean_prompt, **call_options)

    target_name = _filename_for(filename, image.mime_type)
    incoming = storage.IncomingFile(
        filename=target_name,
        stream=io.BytesIO(image.data),
    )
    record = storage.create_standalone_record(
        incoming,
        user,
        extra_fields={
            "aiGeneration": {
                "providerId": str(provider.get("_id") or provider.get("id") or ""),
                "providerName": provider.get("name"),
                "model": image.model or model_id,
                "prompt": clean_prompt,
                "revisedPrompt": image.revised_prompt,
            }
        },
    )

    task_id = interop.queue_record_processing(record["id"])

    return {
        "record_id": record["id"],
        "record": {
            "id": record["id"],
            "name": record.get("name"),
            "mime": record.get("mime"),
            "size": record.get("size"),
            "hash": record.get("hash"),
            "status": record.get("status"),
            "filepath": record.get("filepath"),
        },
        "model": image.model or model_id,
        "revised_prompt": image.revised_prompt,
        "deduplicated": bool(record.get("deduplicated")),
        "task_id": task_id,
    }

