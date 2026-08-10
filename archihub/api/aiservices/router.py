"""AI service routes.

Rewritten rather than ported, and the route table reflects the rewrite: what
used to be "models" (a confusing name for *provider configurations*) is now
``/aiservices/providers``, and the **model catalogue is a separate resource that
is discovered, not stored**.

Roles are unchanged from the legacy blueprint: reading needs ``admin``,
``processing`` or ``llm``; configuring a provider needs ``admin`` or
``processing``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse, Response

from archihub.api.aiservices import catalogue, chat, conversations, providers, streaming
from archihub.api.aiservices import errors as ai_errors
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import (
    LEGACY_ROLE_FAILURE_STATUS,
    CurrentUser,
    get_current_user,
    require_role_any,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/aiservices", tags=["AI services"])

require_reader = require_role_any(
    "admin", "processing", "llm", status_code=LEGACY_ROLE_FAILURE_STATUS
)
require_operator = require_role_any(
    "admin", "processing", status_code=LEGACY_ROLE_FAILURE_STATUS
)

_RESPONSES = {
    401: {"description": "Missing/invalid token, or insufficient role"},
    404: {"description": "No such provider"},
}


def _respond(result) -> JSONResponse:
    payload, status_code = result
    return JSONResponse(status_code=status_code, content=payload)


def _provider_error(exc: ai_errors.ProviderError) -> JSONResponse:
    """Turn a classified provider failure into an HTTP answer.

    The reason travels in the body so the interface can say something useful —
    "the key is wrong" and "the provider is down" need different words and
    different buttons, and the legacy `{'msg': str(e)}` made them
    indistinguishable.
    """
    status = {
        ai_errors.Reason.AUTH: 502,
        ai_errors.Reason.RATE_LIMITED: 429,
        ai_errors.Reason.UNAVAILABLE: 502,
        ai_errors.Reason.TIMEOUT: 504,
        ai_errors.Reason.MODEL_NOT_FOUND: 404,
        ai_errors.Reason.CONTEXT_LENGTH: 413,
        ai_errors.Reason.INVALID_REQUEST: 400,
        ai_errors.Reason.CONTENT_FILTERED: 422,
    }.get(exc.reason, 502)

    return JSONResponse(status_code=status, content={"msg": str(exc), "reason": exc.reason.value})


# ---------------------------------------------------------------------------
# Dialects and providers
# ---------------------------------------------------------------------------


@router.get(
    "/dialects",
    dependencies=[Depends(require_reader)],
    responses={200: {"description": "The wire protocols this build speaks"}},
)
def list_dialects() -> JSONResponse:
    """The protocols available when configuring a provider.

    A *protocol*, not a vendor list. Any endpoint speaking one of these can be
    configured without a code change — which is why the legacy
    `llm_providers = ["OpenAI", "Google", ...]` literal is gone.
    """
    return JSONResponse(status_code=200, content=providers.dialects())


@router.get(
    "/providers",
    dependencies=[Depends(require_reader)],
    responses={200: {"description": "Configured providers, without credentials"}},
)
def list_providers() -> JSONResponse:
    """Every configured provider. Credentials are never included."""
    return _respond(providers.list_providers())


@router.post(
    "/providers",
    status_code=201,
    responses={
        201: {"description": "Provider created"},
        409: {"description": "That name is taken"},
        **_RESPONSES,
    },
)
def create_provider(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_operator),
) -> JSONResponse:
    """Connect this archive to a model endpoint."""
    return _respond(providers.create(body, current_user.username))


@router.get(
    "/providers/{provider_id}",
    dependencies=[Depends(require_reader)],
    responses={200: {"description": "One provider"}, **_RESPONSES},
)
def get_provider(provider_id: str) -> JSONResponse:
    return _respond(providers.get_provider(provider_id))


@router.put(
    "/providers/{provider_id}",
    responses={200: {"description": "Provider updated"}, **_RESPONSES},
)
def update_provider(
    provider_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_operator),
) -> JSONResponse:
    """Change a provider.

    Omitting ``key`` leaves the stored credential alone; sending an empty one
    clears it. The legacy update wrote whatever its model produced, so saving
    the form without retyping the key erased it.
    """
    return _respond(providers.update(provider_id, body, current_user.username))


@router.delete(
    "/providers/{provider_id}",
    responses={200: {"description": "Provider deleted"}, **_RESPONSES},
)
def delete_provider(
    provider_id: str,
    current_user: CurrentUser = Depends(require_operator),
) -> JSONResponse:
    return _respond(providers.delete(provider_id, current_user.username))


@router.get(
    "/providers/{provider_id}/check",
    dependencies=[Depends(require_operator)],
    responses={200: {"description": "Whether the provider answers, and with how many models"}},
)
def check_provider(provider_id: str) -> JSONResponse:
    """Call the provider now and report what happened.

    A live check rather than a stored flag: the question an operator is asking
    while looking at this screen is whether the credential works *now*.
    """
    return _respond(providers.check(provider_id))


# ---------------------------------------------------------------------------
# The model catalogue
# ---------------------------------------------------------------------------


@router.get(
    "/providers/{provider_id}/models",
    dependencies=[Depends(require_reader)],
    responses={
        200: {"description": "Models this provider offers, as it describes them"},
        **_RESPONSES,
    },
)
def list_models(
    provider_id: str,
    refresh: bool = Query(False, description="Bypass the cache and ask the provider again"),
) -> JSONResponse:
    """What this provider offers, discovered from the provider itself.

    Context windows and capabilities are whatever the endpoint reports; where it
    reports nothing, nothing is claimed. If discovery fails, the response says
    so — the legacy code substituted a hardcoded list, so a provider with a bad
    key showed a normal catalogue of models that could not be called.
    """
    provider = providers.load(provider_id)
    if provider is None:
        return JSONResponse(status_code=404, content={"msg": _("Provider not found")})

    return JSONResponse(
        status_code=200, content=catalogue.for_provider(provider, refresh=refresh).as_dict()
    )


@router.put(
    "/providers/{provider_id}/models/{model_id:path}/metadata",
    responses={200: {"description": "Override recorded"}, **_RESPONSES},
)
def set_model_metadata(
    provider_id: str,
    model_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_operator),
) -> JSONResponse:
    """Record what you know about a model that its provider does not report.

    This is the escape hatch that replaces hardcoded tables: an operator whose
    gateway exposes a vision model without saying so records it here, and the
    catalogue reflects it immediately. Model ids may contain slashes
    (``vendor/model``), hence the path converter.
    """
    if providers.load(provider_id) is None:
        return JSONResponse(status_code=404, content={"msg": _("Provider not found")})

    return JSONResponse(
        status_code=200,
        content=catalogue.set_override(provider_id, model_id, body, current_user.username),
    )


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


@router.post(
    "/providers/{provider_id}/chat",
    responses={
        200: {"description": "The model's answer, or an SSE stream of it"},
        413: {"description": "The conversation does not fit, even shortened"},
        502: {"description": "The provider refused or is unreachable"},
        **_RESPONSES,
    },
)
def send_chat(
    provider_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_reader),
) -> Response:
    """Ask a model something.

    ``stream: true`` returns server-sent events. Those are correctly framed —
    real blank-line separators, a ``[DONE]`` terminator, and a failure delivered
    as an ``error`` event rather than a dropped socket, since by then the status
    line is long gone.
    """
    provider = providers.load(provider_id)
    if provider is None:
        return JSONResponse(status_code=404, content={"msg": _("Provider not found")})

    messages = body.get("messages")
    message = conversations.validate_messages(messages)
    if message:
        return JSONResponse(status_code=400, content={"msg": message})
    if not body.get("model"):
        return JSONResponse(status_code=400, content={"msg": _("You must specify a model")})

    options = {
        key: body[key]
        for key in (
            "model", "max_tokens", "max_tokens_field", "temperature", "top_p",
            "stop", "seed", "tools", "tool_choice", "response_format", "reasoning_effort",
        )
        if key in body
    }

    try:
        if body.get("stream"):
            return streaming.response(chat.stream(provider, messages, **options))
        return JSONResponse(status_code=200, content=chat.complete(provider, messages, **options))
    except chat.CapabilityError as exc:
        return JSONResponse(status_code=400, content={"msg": str(exc)})
    except ai_errors.ProviderError as exc:
        return _provider_error(exc)


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


@router.post(
    "/conversation",
    responses={
        200: {"description": "Conversation updated"},
        201: {"description": "Conversation created"},
        **_RESPONSES,
    },
)
def save_conversation(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Start a conversation, or append to one you own."""
    return _respond(conversations.save(body, current_user.username))


@router.post(
    "/conversation/history",
    responses={200: {"description": "Your conversations, newest first"}, **_RESPONSES},
)
def conversation_history(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Your own conversations. Declared before ``/conversation/{id}``."""
    return _respond(conversations.history(body, current_user.username))


@router.get(
    "/conversation/{conversation_id}",
    responses={200: {"description": "The conversation"}, **_RESPONSES},
)
def get_conversation(
    conversation_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    return _respond(conversations.get(conversation_id, current_user.username))


@router.delete(
    "/conversation/{conversation_id}",
    responses={200: {"description": "Conversation deleted"}, **_RESPONSES},
)
def delete_conversation(
    conversation_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Delete one of your own.

    The legacy route filtered on the id alone, so a known id deleted anyone's.
    """
    return _respond(conversations.delete(conversation_id, current_user.username))
