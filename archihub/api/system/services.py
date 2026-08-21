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
    Indexing these arrays directly - ``data[2]['value']`` for the locale, say -
    reads a different setting the moment a document is reordered or extended,
    and reads it successfully, so nothing reports the mistake.
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

    Counts documents rather than asking whether the ``users`` collection exists.
    Collection existence is a weak proxy for "nobody has been set up" - it
    answers the wrong question for a collection created empty - and this is the
    only thing standing in front of an unauthenticated endpoint that creates an
    administrator. Counting documents
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


def _system_capabilities() -> list[str]:
    """Capabilities derived from settings rather than declared by a plugin.

    **These are not decoration; they are what the interface is gated on**, and
    they are only half the list - the other half is declared by plugins. Collect
    one half and the frontend hides every feature behind the other: all four
    download buttons and both "Pregúntale a la IA" entry points simply are not
    rendered, with no error, no request and nothing logged.

    The names are matched with `.includes()` in `upgrade_front`, so they are
    literal and each is derived independently:

    ``llm``             at least one provider is configured
    ``indexing``        Elasticsearch indexing is on
    ``vector_db``       Qdrant indexing is on
    ``files_download``  downloading originals is allowed

    Failures are swallowed per capability rather than for the block as a whole:
    this route is what the login screen bootstraps from, so a Qdrant setting that
    cannot be read must not cost the user their download buttons as well.
    """
    derived: list[str] = []

    def add(name: str, probe) -> None:
        try:
            if probe():
                derived.append(name)
        except Exception:
            logger.warning("Could not determine the %s capability", name, exc_info=True)

    def has_provider() -> bool:
        from archihub.api.aiservices.providers import COLLECTION as PROVIDERS

        # A count, not a listing. Fetching provider records to ask whether any
        # exist reads their stored credentials for no reason.
        return _mongo().count(PROVIDERS, {}) > 0

    add("llm", has_provider)
    add("indexing", lambda: get_setting_value("index_management", "index_activation"))
    add("vector_db", lambda: get_setting_value("index_management", "vector_activation"))
    add("files_download", lambda: get_setting_value("files_management", "files_download"))

    return derived


def get_system_settings() -> tuple[dict, int]:
    """Public bootstrap payload, read by the frontend before anyone logs in.

    Unauthenticated, so it carries only what a login screen needs: whether
    onboarding is pending, the interface language, the version, and which
    optional features are on.

    Capabilities come from plugin metadata via the discovery module. Nothing is
    instantiated: this route is unauthenticated, and constructing every active
    plugin to read a list of names would run plugin code for anonymous callers.

    Plugin capabilities are only half of the list; see `_system_capabilities`
    for the four the instance's own settings decide, and why leaving them out
    silently removed working features from the interface.
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

    capabilities.extend(_system_capabilities())

    # `language`, `capabilities`, `version` - and nothing else.
    #
    # THIS ENDPOINT IS UNAUTHENTICATED: the login screen reads it before anyone
    # has signed in. Every field is therefore public, and one is worth adding
    # only if something reads it. `first_time`, `indexing` and `vector` are
    # deliberately absent - nothing consumes them (`providers.tsx` coerces
    # `!!data.first_time`, so absent behaves exactly as false), and the last two
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
        # Compared, not assumed: a mistyped confirmation would otherwise create
        # the administrator account with a password nobody knows.
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

    error = seed_starter_catalogue(body["typeTemplate"], body["username"])
    if error:
        # The administrator exists by now, so onboarding is over and cannot be
        # retried through this route. Say what is missing rather than a generic
        # failure: the remedy is to create the missing pieces by hand.
        logger.error("Instance configured but the starter catalogue is incomplete: %s", error)
        return {"msg": error}, 500

    logger.info("Instance configured; administrator created and starter catalogue seeded")
    return {"msg": _("System configured successfully")}, 201


#: What each onboarding template provisions: the content types, the metadata
#: forms, and the content type the cataloguing screen opens on.
TYPE_TEMPLATES = {
    "basic": {"types": "simple_post_type", "forms": ("simple_form",), "default_type": "carpeta"},
    "detailed": {
        "types": "detailed_post_type",
        "forms": ("isadg_form", "dublin_form"),
        "default_type": "unidad-documental",
    },
}

#: The two controlled vocabularies the authorisation layer resolves through.
ROLES_LIST_NAME = "Roles"
ACCESS_RIGHTS_LIST_NAME = "Niveles de acceso"


def seed_starter_catalogue(template: str, user: str) -> str | None:
    """Provision the content types, forms and vocabularies a new instance needs.

    Returns an error message, or ``None``.

    WHY THIS IS NOT OPTIONAL. An instance with settings and an administrator but
    none of this cannot be used at all: there is no content type to catalogue
    into, no form to describe anything with, and - most easily missed - no roles
    or access levels, because BOTH ARE RESOLVED THROUGH LIST IDS STORED IN THE
    ``access_rights`` SETTING. Creating the lists is not enough; the setting has
    to be pointed at them, or every role lookup resolves an id that is not
    there and the vocabularies come back empty.

    Idempotent: each piece is created only when it is absent, so a partially
    seeded instance can be completed rather than duplicated.
    """
    from archihub.api.forms.services import create as create_form
    from archihub.api.lists.services import create as create_list
    from archihub.api.system import default_settings
    from archihub.api.types.services import create as create_type

    plan = TYPE_TEMPLATES.get(template)
    if plan is None:
        return _("Unknown type template")

    mongo = _mongo()

    # Forms first: a content type names its form by slug, so seeding the type
    # first leaves it pointing at something that does not exist yet.
    for form_name in plan["forms"]:
        form = getattr(default_settings, form_name)
        if mongo.get_record("forms", {"slug": form["slug"]}):
            continue
        payload, status = create_form(dict(form), user)
        if status != 201:
            return payload.get("msg", _("Could not create the metadata form"))

    declared = getattr(default_settings, plan["types"])
    for post_type in declared if isinstance(declared, list) else [declared]:
        if mongo.get_record("post_types", {"slug": post_type["slug"]}):
            continue
        payload, status = create_type(dict(post_type), user)
        if status != 201:
            return payload.get("msg", _("Could not create the content type"))

    for vocabulary in default_settings.roles_rights_settings:
        if mongo.get_record("lists", {"name": vocabulary["name"]}):
            continue
        payload, status = create_list(dict(vocabulary), user)
        if status != 201:
            return payload.get("msg", _("Could not create the list"))

    _set_default_cataloguing_type(plan["default_type"])
    return _link_authorisation_vocabularies()


def _set_default_cataloguing_type(slug: str) -> None:
    """Point `tipo_defecto` at the template's main content type.

    The cataloguing screen routes to `/cataloging/<value>`, so leaving it empty
    sends it to `/cataloging/undefined`. Only filled when unset, so an operator
    who has already chosen keeps their choice.
    """
    mongo = _mongo()
    record = mongo.get_record(COLLECTION, {"name": "post_types_settings"})
    if not record:
        return

    data = record.get("data") or []
    for entry in data:
        if entry.get("id") == "tipo_defecto" and not entry.get("value"):
            entry["value"] = slug
            mongo.update_record(COLLECTION, {"name": "post_types_settings"}, {"data": data})
            return


def _link_authorisation_vocabularies() -> str | None:
    """Point the `access_rights` setting at the two vocabulary lists.

    This is the step whose absence is hardest to diagnose: roles and access
    levels are read by resolving the list id stored here, so without it both
    come back empty while the lists themselves sit in the database looking
    correct.
    """
    mongo = _mongo()

    roles = mongo.get_record("lists", {"name": ROLES_LIST_NAME})
    rights = mongo.get_record("lists", {"name": ACCESS_RIGHTS_LIST_NAME})
    if not roles or not rights:
        return _("Could not create the list")

    record = mongo.get_record(COLLECTION, {"name": "access_rights"})
    if not record:
        return _("The access_rights settings document does not exist")

    wanted = {"user_roles_list": str(roles["_id"]), "access_rights_list": str(rights["_id"])}
    data = record.get("data") or []
    changed = False
    for entry in data:
        target = wanted.get(entry.get("id"))
        if target and not entry.get("value"):
            entry["value"] = target
            changed = True

    if changed:
        mongo.update_record(COLLECTION, {"name": "access_rights"}, {"data": data})
    return None


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
    """Installed plugins and whether each is active.

    The set is read from the plugins directory, never from a list written down
    in the source. Installing a plugin is documented as copying it into that
    directory and rebuilding, and this listing is the only screen it can be
    activated from - so a name this route does not know is a plugin an operator
    cannot switch on.

    Read statically, without importing: rendering an administration screen must
    not execute code from every directory that happens to be present, including
    directories nobody has activated.
    """
    try:
        from archihub.plugins.framework.discovery import (
            get_active_plugin_slugs,
            list_installed_plugins,
            read_manifest,
        )

        active = set(get_active_plugin_slugs())

        # Union rather than the directory alone: a plugin that was deleted from
        # disk while still switched on has to stay visible, or it becomes
        # impossible to switch off from here.
        plugins = []
        for slug in sorted(set(list_installed_plugins()) | active):
            manifest = read_manifest(slug)
            info = manifest.info
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
                    # `undefined.map`. This payload is a deliberately chosen
                    # subset of `plugin_info`, so a field the interface needs has
                    # to be chosen explicitly.
                    #
                    # Defaulted to `[]` rather than omitted: an installed plugin
                    # that declares no screens is a real case (`liquidText`), and
                    # the difference between "no screens" and "field absent" is
                    # exactly what broke the page.
                    "type": _screen_list(info.get("type")),
                    # Whether this backend can build the plugin, and if not,
                    # why. Reported rather than used to hide the row: someone
                    # who has just installed a plugin needs to be told what is
                    # wrong with it, which is more useful than its absence.
                    "supported": manifest.mountable,
                    "installed": manifest.installed,
                    "problem": manifest.problem,
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
    from archihub.plugins.framework.discovery import _validate_slug, read_manifest

    try:
        _validate_slug(slug)
    except Exception:
        return {"msg": _("Invalid plugin")}, 400

    # Only ACTIVATION is gated. Switching a plugin off has to keep working
    # whatever state it is in - a plugin this backend cannot build is exactly
    # the one an operator needs to deactivate, and refusing that leaves editing
    # the settings document by hand as the only way out.
    if active:
        manifest = read_manifest(slug)
        if not manifest.installed:
            return {"msg": _("This plugin is not installed on this instance")}, 404
        if not manifest.mountable:
            return {
                "msg": _(
                    "This plugin is not supported by this backend yet and cannot be activated"
                ),
                # The generic message is what the interface shows; the specific
                # one is what makes it actionable.
                "detail": manifest.problem,
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
    """Flip a plugin's activation, refusing a slug that is not installed.

    Existence is a question about the plugins directory. Whether the plugin can
    be *mounted* is a different question, asked by ``set_plugin_active`` and
    only in the activating direction - so this route can always switch one off.
    """
    from archihub.plugins.framework.discovery import _validate_slug, is_installed

    try:
        _validate_slug(slug)
    except Exception:
        return {"msg": _("Plugin does not exist")}, 404

    record = get_setting("active_plugins") or {}
    currently_active = slug in (record.get("data") or [])

    if not currently_active and not is_installed(slug):
        return {"msg": _("Plugin does not exist")}, 404

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
    is also the Celery broker, meaning queued jobs go with it. Narrowing it needs
    namespaced keys or a separate database number to exist first; until then the
    breadth is a known property of the operation rather than an oversight.
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

    A broker that is down answers 503, not 200 and not 500. The distinction is
    for the operator reading it: 503 says the queue is unreachable, where a
    generic 500 carrying a connection string says only that something broke.
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
        # Checked before use: a missing schema is a configuration problem the
        # operator can act on, not an internal error.
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
