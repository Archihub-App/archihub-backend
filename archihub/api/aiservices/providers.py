"""Configured providers — endpoints an archive can call.

A provider is **data**: a dialect, a base URL, a credential, some headers. There
is no Python class per vendor, so connecting an archive to a provider nobody has
heard of is a POST, not a release. A list of vendor names in source makes
everything absent from it unreachable, and the list is never complete.

**Credentials are encrypted at rest and never leave.** The key is excluded by
the single serialiser that builds every response, rather than by each query
remembering to project it away — one place to be right instead of many places to
forget. It is reported as a fingerprint, so an operator can tell two keys apart
without seeing either.
"""

from __future__ import annotations

import datetime
import hashlib
import logging

from bson.objectid import ObjectId

from archihub.api.aiservices import catalogue
from archihub.api.aiservices.dialects import DIALECTS, DIALECT_NAMES
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

COLLECTION = "llm_models"

#: What a client may set. `key` is handled separately because it is encrypted on
#: the way in and never sent back out.
CLIENT_FIELDS = ("name", "dialect", "base_url", "headers", "default_model", "enabled")

REQUIRED_FIELDS = ("name", "dialect")

#: Header names a provider record may not set, because they are ours to control
#: or would leak the credential somewhere it should not go.
RESERVED_HEADERS = frozenset({"authorization", "x-api-key", "cookie", "host", "content-length"})


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _object_id(value):
    try:
        return ObjectId(value)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def _fernet():
    from cryptography.fernet import Fernet

    from archihub.core.settings import get_settings

    return Fernet(get_settings().fernet_key)


def encrypt_key(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt_key(stored: str | None) -> str | None:
    """The usable credential, or ``None``.

    A key that will not decrypt is a configuration fault, not a crash: it is
    logged and the provider simply has no credential, so discovery reports an
    auth failure the operator can act on instead of a stack trace.
    """
    if not stored:
        return None
    try:
        return _fernet().decrypt(stored.encode()).decode()
    except Exception:
        logger.error("A stored provider credential could not be decrypted; check FERNET_KEY")
        return None


def fingerprint(stored: str | None) -> str | None:
    """A short, stable, non-reversible tag for a credential.

    Lets an operator confirm *which* key is configured without the value ever
    being returned. Derived from the ciphertext, so it changes when the key does
    and reveals nothing about the secret.
    """
    if not stored:
        return None
    return hashlib.sha256(stored.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def present(provider: dict) -> dict:
    """A provider as the API returns it. **Never includes the credential.**"""
    return {
        "id": str(provider.get("_id") or provider.get("id") or ""),
        "name": provider.get("name"),
        "dialect": provider.get("dialect"),
        "base_url": provider.get("base_url"),
        "headers": provider.get("headers") or {},
        "default_model": provider.get("default_model"),
        "enabled": provider.get("enabled", True),
        "has_key": bool(provider.get("key")),
        "key_fingerprint": fingerprint(provider.get("key")),
        "createdAt": _iso(provider.get("createdAt")),
        "updatedAt": _iso(provider.get("updatedAt")),
    }


def _iso(value):
    return value.isoformat() if isinstance(value, datetime.datetime) else value


def dialects() -> list[dict]:
    """The wire protocols this build speaks, for a provider-creation form."""
    return [
        {
            "id": name,
            "label": adapter.label,
            "requires_base_url": adapter.requires_base_url,
            "default_base_url": getattr(adapter, "default_base_url", None),
        }
        for name, adapter in sorted(DIALECTS.items())
    ]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(payload: dict, *, creating: bool) -> str | None:
    if creating:
        for field in REQUIRED_FIELDS:
            if not payload.get(field):
                return _("{field} is missing", field=field)

    dialect = payload.get("dialect")
    if dialect is not None:
        if dialect not in DIALECT_NAMES:
            return _('Unknown dialect "{dialect}"', dialect=str(dialect)[:40])
        adapter = DIALECTS[dialect]
        if adapter.requires_base_url and creating and not payload.get("base_url"):
            return _("This dialect requires a base URL")

    base_url = payload.get("base_url")
    if base_url is not None:
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            return _("The base URL must be an http or https address")

    headers = payload.get("headers")
    if headers is not None:
        if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
        ):
            return _("Headers must be a map of text values")
        for name in headers:
            if name.lower() in RESERVED_HEADERS:
                return _('The "{header}" header cannot be set here', header=name)

    if "enabled" in payload and not isinstance(payload["enabled"], bool):
        return _("enabled must be true or false")

    return None


def _client_fields(body: dict) -> dict:
    return {key: body[key] for key in CLIENT_FIELDS if key in body}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def list_providers() -> tuple[list, int]:
    rows = _mongo().get_all_records(COLLECTION, {}, sort=[("name", 1)])
    return [present(row) for row in rows], 200


def get_provider(provider_id: str) -> tuple[dict, int]:
    provider = load(provider_id)
    if provider is None:
        return {"msg": _("Provider not found")}, 404
    return present(provider), 200


def load(provider_id: str) -> dict | None:
    """The raw record, credential included. For internal callers only."""
    object_id = _object_id(provider_id)
    if object_id is None:
        return None
    return _mongo().get_record(COLLECTION, {"_id": object_id})


def create(body: dict, user: str) -> tuple[dict, int]:
    payload = _client_fields(body)
    message = _validate(payload, creating=True)
    if message:
        return {"msg": message}, 400

    if _mongo().get_record(COLLECTION, {"name": payload["name"]}, fields={"_id": 1}):
        return {"msg": _("A provider with that name already exists")}, 409

    payload["key"] = encrypt_key(body.get("key"))
    payload.setdefault("enabled", True)
    payload["createdBy"] = user
    payload["createdAt"] = _now()
    payload["updatedAt"] = payload["createdAt"]

    inserted = _mongo().insert_record(COLLECTION, payload)
    provider_id = str(inserted.inserted_id)
    _audit(user, "llm_provider_create", {"provider": provider_id, "name": payload["name"]})

    return {"msg": _("Provider created successfully"), "id": provider_id}, 201


def update(provider_id: str, body: dict, user: str) -> tuple[dict, int]:
    provider = load(provider_id)
    if provider is None:
        return {"msg": _("Provider not found")}, 404

    payload = _client_fields(body)
    message = _validate(payload, creating=False)
    if message:
        return {"msg": message}, 400

    if payload.get("name") and payload["name"] != provider.get("name"):
        clash = _mongo().get_record(COLLECTION, {"name": payload["name"]}, fields={"_id": 1})
        if clash:
            return {"msg": _("A provider with that name already exists")}, 409

    # An absent `key` leaves the stored one alone; an explicit empty string
    # clears it. The legacy update wrote whatever the model produced, so saving
    # the settings form without retyping the key erased it.
    if "key" in body:
        payload["key"] = encrypt_key(body["key"]) if body["key"] else None

    if not payload:
        return {"msg": _("Nothing to update")}, 400

    payload["updatedBy"] = user
    payload["updatedAt"] = _now()
    _mongo().update_record(COLLECTION, {"_id": provider["_id"]}, payload)

    # Anything that changes where or how we call invalidates what we believe is
    # there.
    catalogue.clear_cache(provider_id)
    _audit(user, "llm_provider_update", {"provider": provider_id, "fields": sorted(payload)})

    return {"msg": _("Provider updated successfully")}, 200


def delete(provider_id: str, user: str) -> tuple[dict, int]:
    provider = load(provider_id)
    if provider is None:
        return {"msg": _("Provider not found")}, 404

    _mongo().delete_record(COLLECTION, {"_id": provider["_id"]})
    _mongo().delete_records(catalogue.OVERRIDES_COLLECTION, {"provider": provider_id})
    catalogue.clear_cache(provider_id)
    _audit(user, "llm_provider_delete", {"provider": provider_id})

    return {"msg": _("Provider deleted successfully")}, 200


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def check(provider_id: str) -> tuple[dict, int]:
    """Can we reach this provider, and what does it offer?

    A real call rather than a stored flag, because the useful question at the
    moment an operator is looking at a settings screen is whether the credential
    works *now*.
    """
    provider = load(provider_id)
    if provider is None:
        return {"msg": _("Provider not found")}, 404

    result = catalogue.for_provider(provider, refresh=True)
    return {
        "ok": result.error is None,
        "models": len(result.models),
        "error": result.error,
        "reason": result.reason,
    }, 200


def _audit(user: str | None, action: str, details: dict) -> None:
    from archihub.api.logs.services import register_log

    register_log(user, action, details)
