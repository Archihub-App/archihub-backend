"""Instance settings and onboarding.

Port of ``app/api/system/services.py``.

SCOPE: settings read/write, the seeder, plugin activation, cache clearing and
first-time onboarding. The maintenance routes that rebuild Elasticsearch indexes
or load geometry (`regenerate-index`, `index-resources`, `index-geometries`,
`geo-load`, and the two file-cleanup routes) are NOT ported here - they belong
with the `search` and `geosystem` domains, which own the handlers they drive.

The ``system`` collection holds one document per settings group, addressed by
``name``. Each has a ``data`` array of ``{id, value, ...}`` entries.
"""

from __future__ import annotations

import json
import logging

from bson import json_util

from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

COLLECTION = "system"

# Settings groups that are never returned by the generic listing: the plugin
# registry is managed through its own routes and is not user-editable settings.
_HIDDEN_SETTINGS = {"active_plugins"}


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def parse_result(result):
    return json.loads(json_util.dumps(result))


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def set_system_setting() -> None:
    """Create missing settings documents and add newly-introduced entries.

    Idempotent and additive: an existing document keeps its stored values, and
    only entries it has never seen are appended. That is what allows a new
    release to introduce a setting without resetting the ones an operator has
    already configured.
    """
    from archihub.api.system.default_settings import settings as defaults

    mongo = _mongo()

    for setting in defaults:
        existing = mongo.get_record(COLLECTION, {"name": setting["name"]})

        if not existing:
            mongo.insert_record(COLLECTION, dict(setting))
            logger.info("Seeded settings group %s", setting["name"])
            continue

        known = {entry.get("id") for entry in existing.get("data") or []}
        added = [entry for entry in setting.get("data") or [] if entry.get("id") not in known]

        if added:
            mongo.update_record(
                COLLECTION,
                {"name": setting["name"]},
                {"data": (existing.get("data") or []) + added},
            )
            logger.info(
                "Added %d new entr%s to settings group %s",
                len(added),
                "y" if len(added) == 1 else "ies",
                setting["name"],
            )


# ---------------------------------------------------------------------------
# Reading and writing settings
# ---------------------------------------------------------------------------


def get_all_settings() -> tuple[dict, int]:
    try:
        records = _mongo().get_all_records(
            COLLECTION, {"name": {"$nin": list(_HIDDEN_SETTINGS)}}
        )
        return {"settings": parse_result(list(records))}, 200
    except Exception as exc:
        logger.exception("Could not read settings")
        return {"msg": str(exc)}, 500


def get_setting(name: str) -> dict | None:
    return _mongo().get_record(COLLECTION, {"name": name})


def get_setting_value(name: str, entry_id: str, fallback_index: int | None = None):
    """Read one entry's value from a settings group.

    Looks the entry up by id, falling back to a position only when given one.
    The legacy code indexed these arrays directly (``data[2]['value']`` for the
    locale, ``data[0]``/``data[1]`` for the self-service toggles), which reads
    the wrong setting the moment a document is reordered or extended.
    """
    record = get_setting(name)
    data = (record or {}).get("data") or []

    entry = next((item for item in data if item.get("id") == entry_id), None)
    if entry is None and fallback_index is not None and len(data) > fallback_index:
        entry = data[fallback_index]

    return (entry or {}).get("value")


def update_option(name: str, data: dict) -> None:
    """Update named entries within one settings group."""
    record = get_setting(name)
    if not record:
        logger.warning("Cannot update unknown settings group %s", name)
        return

    entries = record.get("data") or []
    for entry in entries:
        if entry.get("id") in data:
            entry["value"] = data[entry["id"]]

    _mongo().update_record(COLLECTION, {"name": name}, {"data": entries})


def update_settings(body: dict, current_user: str) -> tuple[dict, int]:
    """Apply a settings update from the admin UI.

    Only entries that already exist in a group are written. A client cannot
    introduce new settings keys, which would otherwise let arbitrary data
    accumulate in a document the application reads by position elsewhere.
    """
    try:
        for name, values in (body or {}).items():
            if name in _HIDDEN_SETTINGS:
                logger.warning("Refusing to update protected settings group %s", name)
                continue
            if isinstance(values, dict):
                update_option(name, values)

        clear_system_cache()
        _register_log(current_user, "system_update", {"settings": list((body or {}).keys())})
        return {"msg": _("Settings updated successfully")}, 200
    except Exception as exc:
        logger.exception("Could not update settings")
        return {"msg": str(exc)}, 500


def get_access_rights_id():
    """Id of the list that defines access rights."""
    from archihub.core.roles import get_access_rights_id as _get

    return _get()


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------


def is_first_time() -> bool:
    """Whether the instance still needs its initial administrator.

    STRICTER THAN THE LEGACY CHECK, deliberately. That asked whether the
    ``users`` *collection* existed. Collection existence is a weak proxy for
    "nobody has been set up": it answers the wrong question if a collection is
    created empty, and it is the only thing standing in front of an
    unauthenticated endpoint that creates an administrator. Counting documents
    asks the question that actually matters.
    """
    mongo = _mongo()
    try:
        return mongo.count("users", {}) == 0
    except Exception:
        logger.exception("Could not determine onboarding state")
        # Fail closed: if it cannot be established that the instance is
        # unconfigured, do not offer to configure it.
        return False


def get_system_settings() -> tuple[dict, int]:
    """Public bootstrap payload, read by the frontend before anyone logs in.

    Unauthenticated, so it carries only what a login screen needs: whether
    onboarding is pending, the interface language, the version, and which
    optional features are on.

    The legacy version imported AND INSTANTIATED every active plugin here to
    collect capabilities - on an unauthenticated route. Capabilities now come
    from plugin metadata via the discovery module, with no instantiation.
    """
    if is_first_time():
        return {"first_time": True}, 200

    from archihub import __version__

    capabilities: list[str] = []
    try:
        from archihub.plugins.framework.discovery import get_active_plugin_slugs, get_plugin_info

        for slug in get_active_plugin_slugs():
            capabilities.extend(get_plugin_info(slug).get("capabilities") or [])
    except Exception:
        logger.warning("Could not collect plugin capabilities", exc_info=True)

    return {
        "first_time": False,
        "version": __version__,
        "language": get_setting_value("user_management", "user_languages", 2) or "en",
        "indexing": bool(get_setting_value("index_management", "index_activation", 0)),
        "vector": bool(get_setting_value("index_management", "vector_activation", 1)),
        "capabilities": sorted(set(capabilities)),
    }, 200


def set_first_time(body: dict) -> tuple[dict, int]:
    """Create the initial administrator and seed a starter catalogue.

    UNAUTHENTICATED BY NECESSITY - there is nobody to authenticate as yet. The
    onboarding check is therefore the only thing preventing anyone from creating
    an administrator, so it is re-evaluated here rather than trusted from a
    previous call, and it counts users rather than inspecting collections.
    """
    if not is_first_time():
        return {"msg": _("The system is already configured")}, 400

    required = ("username", "password", "confirmPassword", "typeTemplate")
    if any(not body.get(field) for field in required):
        return {"msg": _("All fields are required")}, 400

    if body["password"] != body["confirmPassword"]:
        # The legacy version never compared these, so a mistyped confirmation
        # silently created an account with the first value.
        return {"msg": _("Passwords do not match")}, 400

    set_system_setting()

    from archihub.api.users.services import register_user

    result, status = register_user(
        {
            "username": body["username"],
            "name": body["username"],
            "password": body["password"],
            "roles": [
                {"id": role}
                for role in ("admin", "editor", "user", "super_editor", "publisher")
            ],
            "accessRights": [],
            "verified": True,
        }
    )
    if status != 201:
        return {"msg": result.get("msg", _("Could not create the administrator"))}, status

    logger.info("Instance configured; initial administrator created")
    return {"msg": _("System configured successfully")}, 201


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------


def get_plugins() -> tuple[list | dict, int]:
    """Installed plugins and whether each is active."""
    try:
        from archihub.plugins.framework.discovery import get_active_plugin_slugs, get_plugin_info
        from archihub.plugins.framework.ported_registry import PORTED_PLUGINS

        active = set(get_active_plugin_slugs())

        plugins = []
        for slug in sorted(PORTED_PLUGINS | active):
            info = get_plugin_info(slug)
            plugins.append(
                {
                    "slug": slug,
                    "name": info.get("name", slug),
                    "description": info.get("description", ""),
                    "version": info.get("version"),
                    "author": info.get("author"),
                    "active": slug in active,
                    # Surfaced so an operator can see why a plugin cannot be
                    # activated on this backend.
                    "supported": slug in PORTED_PLUGINS,
                }
            )
        return plugins, 200
    except Exception as exc:
        logger.exception("Could not list plugins")
        return {"msg": str(exc)}, 500


def set_plugin_active(slug: str, active: bool, current_user: str) -> tuple[dict, int]:
    """Activate or deactivate a plugin.

    Activating one that this backend cannot mount is refused rather than
    written: the setting would take effect at the next restart and the instance
    would then fail to start.
    """
    from archihub.plugins.framework.discovery import _validate_slug
    from archihub.plugins.framework.ported_registry import PORTED_PLUGINS

    try:
        _validate_slug(slug)
    except Exception:
        return {"msg": _("Invalid plugin")}, 400

    if active and slug not in PORTED_PLUGINS:
        return {
            "msg": _(
                "This plugin is not supported by this backend yet and cannot be activated"
            )
        }, 400

    mongo = _mongo()
    record = mongo.get_record(COLLECTION, {"name": "active_plugins"}) or {}
    current = list(record.get("data") or [])

    if active and slug not in current:
        current.append(slug)
    elif not active and slug in current:
        current.remove(slug)
    else:
        return {"msg": _("No changes were made")}, 200

    mongo.update_record(COLLECTION, {"name": "active_plugins"}, {"data": current})
    clear_system_cache()
    _register_log(current_user, "system_update", {"plugin": slug, "active": active})

    return {
        "msg": _("Plugin updated successfully. Restart the application to apply the change")
    }, 200


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def clear_system_cache() -> None:
    """Drop cached settings-derived state within this process."""
    from archihub.core.i18n import reset_locale_cache

    reset_locale_cache()


def clear_cache() -> tuple[dict, int]:
    """Flush the shared cache.

    NOTE: this is a Redis ``FLUSHDB``, so it clears the entire database - which
    is also the Celery broker. Preserved from the legacy behaviour, and recorded
    as a finding rather than changed here, because narrowing it needs namespaced
    keys or a separate database number to be introduced first.
    """
    from archihub.infra.cache import get_cache

    get_cache().clear_cache()
    clear_system_cache()
    return {"msg": _("Cache cleared successfully")}, 200


def _register_log(user: str, action_key: str, metadata: dict) -> None:
    try:
        from archihub.api.logs.services import register_log

        register_log(user, action_key, metadata)
    except ImportError:
        logger.debug("logs domain not ported yet; audit entry %s not written", action_key)
