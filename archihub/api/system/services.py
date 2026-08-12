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

    # `language`, `capabilities`, `version` - and nothing else, which is what
    # the legacy route returns.
    #
    # THIS ENDPOINT IS UNAUTHENTICATED: the login screen reads it before anyone
    # has signed in. So every field is public, and a field is worth adding only
    # if something reads it. An earlier revision also returned `first_time`,
    # `indexing` and `vector`; none has a consumer (`providers.tsx` coerces
    # `!!data.first_time`, so absent behaves exactly as false) and the last two
    # restate what `capabilities` already carries.
    return {
        "version": __version__,
        "language": get_setting_value("user_management", "user_languages", 2) or "en",
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


def _screen_list(declared) -> list[str]:
    """The screens a plugin declares, as a list of strings.

    `plugin_info` is authored per plugin - including by third parties - so its
    shape is not guaranteed. Only a list or tuple of strings is accepted; a bare
    string is *not* iterated, because `list("bulk")` is `["b", "u", "l", "k"]`
    and the frontend would render four buttons pointing at routes that do not
    exist. Anything else becomes an empty list and the plugin renders without
    buttons.
    """
    if isinstance(declared, str):
        return [declared]
    if isinstance(declared, (list, tuple)):
        return [item for item in declared if isinstance(item, str)]
    return []


def get_plugins() -> tuple[dict, int]:
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
                    # The screens this plugin offers - "settings", "bulk",
                    # "lunch", "control". `/processing` renders one button per
                    # entry and routes to `/processing/{type}/{slug}`, so a
                    # plugin whose `type` is missing crashes that page on
                    # `undefined.map`. Legacy returned the whole `plugin_info`,
                    # which carried this incidentally; this payload is a chosen
                    # subset, and the field was not chosen.
                    #
                    # Defaulted to `[]` rather than omitted: an installed plugin
                    # that declares no screens is a real case (`liquidText`), and
                    # the difference between "no screens" and "field absent" is
                    # exactly what broke the page.
                    "type": _screen_list(info.get("type")),
                    # Surfaced so an operator can see why a plugin cannot be
                    # activated on this backend.
                    "supported": slug in PORTED_PLUGINS,
                }
            )
        # Wrapped, not bare: `SystemService.getListPlugins`'s two callers both
        # read `response.plugins`.
        return {"plugins": plugins}, 200
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


def toggle_plugin(slug: str, current_user: str) -> tuple[dict, int]:
    """Flip a plugin's activation, refusing an unknown slug.

    The legacy version listed the ``app/plugins`` directory to decide whether a
    plugin exists, so it accepted the name of any directory present on disk -
    including one this backend cannot mount. It also toggled first and checked
    afterwards in the activation direction.
    """
    from archihub.plugins.framework.discovery import _validate_slug
    from archihub.plugins.framework.ported_registry import PORTED_PLUGINS

    try:
        _validate_slug(slug)
    except Exception:
        return {"msg": _("Plugin does not exist")}, 404

    if slug not in PORTED_PLUGINS:
        return {"msg": _("Plugin does not exist")}, 404

    record = get_setting("active_plugins") or {}
    currently_active = slug in (record.get("data") or [])
    return set_plugin_active(slug, not currently_active, current_user)


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


# ---------------------------------------------------------------------------
# Index maintenance
# ---------------------------------------------------------------------------
# These four queue the long-running jobs in `archihub/worker/tasks/`. Each
# returns as soon as the message is on the broker; the admin screen then follows
# it through `/tasks`. The work itself, and what changed in it, is documented in
# those task modules - not here.


def _queue(task, user: str, name: str, message: str, *args) -> tuple[dict, int]:
    """Queue a task, record it against ``user``, and report it as accepted.

    A broker that is down answers 503 rather than 200. The legacy version had
    no such branch: ``.delay()`` raised, the surrounding ``except Exception``
    turned it into ``{'msg': str(e)}, 500``, and an operator saw a connection
    error where a queue outage was the actual news.
    """
    try:
        queued = task.delay(*args)
    except Exception:
        logger.exception("Could not queue %s", name)
        return {"msg": _("The task queue is unavailable")}, 503

    try:
        from archihub.api.tasks.services import add_task

        add_task(queued.id, name, user, "msg")
    except Exception:
        # The job is already queued. Failing the request now would tell the
        # operator it had not started when it had, and they would start it again.
        logger.warning("Task %s queued but not recorded", queued.id)

    return {"msg": message}, 200


def _require_indexing(missing_msg: str, disabled_msg: str) -> tuple[dict, int] | None:
    """``None`` when indexing is on; the refusal to return otherwise."""
    record = get_setting("index_management")
    if not record:
        return {"msg": missing_msg}, 404
    if not get_setting_value("index_management", "index_activation", 0):
        return {"msg": disabled_msg}, 400
    return None


def regenerate_index(user: str) -> tuple[dict, int]:
    """Queue a rebuild of the resources index under the current schema."""
    from archihub.api.search.mapping import build_resources_mapping
    from archihub.worker.tasks.indexing import regenerate_index_task

    refusal = _require_indexing(
        _("The field for the index management doesn't exists in the system"),
        _("Indexing is deactivated"),
    )
    if refusal:
        return refusal

    schema = get_setting("resources-schema")
    if not schema:
        # The legacy code subscripted this straight away, so a missing schema
        # was a TypeError reported as a 500 with the raw message.
        return {"msg": _("The field for the index management doesn't exists in the system")}, 404

    mapping = build_resources_mapping(schema.get("data") or {})

    return _queue(
        regenerate_index_task,
        user,
        "system.regenerate_index",
        _("The process has been added to the processing queue"),
        mapping,
        user,
    )


def index_resources(user: str) -> tuple[dict, int]:
    """Queue a full reindex of every resource."""
    from archihub.worker.tasks.indexing import index_resources_task

    refusal = _require_indexing(
        _("The index_management record does not exist"), _("Indexing is not enabled")
    )
    if refusal:
        return refusal

    return _queue(
        index_resources_task,
        user,
        "system.index_resources",
        _("The full content indexing task was added to the processing queue"),
    )


def regenerate_index_geometries(user: str) -> tuple[dict, int]:
    """Queue a rebuild of the geometry index.

    Deliberately NOT gated on `index_activation`, matching the legacy route:
    the boundary layer is drawn from Elasticsearch whether or not resource
    search is switched on.
    """
    from archihub.worker.tasks.geometries import regenerate_index_shapes

    return _queue(
        regenerate_index_shapes,
        user,
        "geosystem.regenerate_index_shapes",
        _("Geometry regeneration started"),
    )


def index_geometries(user: str) -> tuple[dict, int]:
    """Queue an indexing pass over the stored boundary shapes."""
    from archihub.worker.tasks.geometries import index_shapes

    return _queue(index_shapes, user, "geosystem.index_shapes", _("Geometry indexing started"))


def _register_log(user: str, action_key: str, metadata: dict) -> None:
    try:
        from archihub.api.logs.services import register_log

        register_log(user, action_key, metadata)
    except ImportError:
        logger.debug("logs domain not ported yet; audit entry %s not written", action_key)
