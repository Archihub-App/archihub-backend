"""``ArchiPlugin`` — the base every plugin builds on.

COMPOSITION, NOT AN ``APIRouter`` SUBCLASS, AND THAT IS WHAT MAKES THE
AUTHORISATION RULE ENFORCEABLE
----------------------------------------------------------------------

A plugin holds a router; it is not one. That is the whole reason a plugin's
routes can be ordinary functions with ordinary dependencies.

**A role requirement on a plugin route is a DEPENDENCY.** It is resolved before
the handler body runs, so there is no return value for a handler to inspect,
forget to inspect, or drop. ``require_roles`` below is the only way a plugin
states one, and two guards hold that shape: a test walks every plugin route's
dependency list, and an AST scan asserts no handler calls a role check itself.

WHAT ELSE THIS CLASS OWNS

* Plugin metadata, and translating the display strings in it.
* The per-plugin settings document in the `system` collection.
* The three routes every plugin gets for free: ``/image``,
  ``/settings/{type}`` and ``POST /settings``.

Cross-collection writes (``update_data``) and the node cache broadcast live in
``framework/data.py`` — they need no instance, and a plugin should not have to
construct itself inside a Celery task merely to reach them.
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any, Callable, Iterable, Sequence

from fastapi import APIRouter, Depends, Form
from fastapi.responses import JSONResponse

from archihub.core.i18n import gettext as _
from archihub.core.responses import json_response
from archihub.core.security.jwt import (
    ROLE_FAILURE_STATUS,
    CurrentUser,
    require_role_any,
)

logger = logging.getLogger(__name__)

#: Keys whose string values are shown to a user and so get translated.
TRANSLATABLE_KEYS = frozenset({"name", "label", "title", "text", "description", "placeholder"})

SETTINGS_ROLES = ("admin", "processing")


def require_roles(*roles: str):
    """A role requirement for a plugin route.

    Always a dependency, never a call inside a handler — see the module
    docstring. Refusal is `ROLE_FAILURE_STATUS`: 403, because the caller is
    known and signing in again cannot help. 401 means only "I do not know who
    you are".
    """
    return require_role_any(*roles)


def translate_display(value: Any, parent_key: str | None = None) -> Any:
    """Translate the human-readable strings in a settings/actions structure.

    Recurses through dicts and lists, translating a string only when its key is
    one a person reads. Everything else — ids, endpoints, role names, default
    values — passes through untouched, which is what stops a setting's `id`
    from being translated into something no lookup will match.
    """
    if isinstance(value, dict):
        return {key: translate_display(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [translate_display(item, parent_key) for item in value]
    if isinstance(value, str) and parent_key in TRANSLATABLE_KEYS:
        return _(value)
    return value


class ArchiPlugin:
    """One plugin: metadata, settings, and an ``APIRouter``.

    Composition rather than ``APIRouter`` subclassing: holding a router means a
    plugin's routes are ordinary functions with ordinary dependencies, which is
    what makes them testable and what makes the role dependency above possible.
    """

    #: Set by each plugin package.
    slug: str = ""

    def __init__(self, slug: str, info: dict, module_file: str | None = None) -> None:
        self.slug = slug
        self.info = dict(info)
        self.module_file = module_file

        self.name = self.info.get("name", slug)
        self.description = self.info.get("description", "")
        self.version = self.info.get("version", "")
        self.author = self.info.get("author", "")
        self.type = self.info.get("type") or []
        self.settings = self.info.get("settings") or {}
        self.actions = self.info.get("actions") or []
        self.capabilities = self.info.get("capabilities") or []

        self.router = APIRouter(prefix=f"/{slug}", tags=[f"Plugin: {slug}"])

    # -- metadata -------------------------------------------------------

    def translated_settings(self) -> dict:
        """The settings tree with its display strings translated.

        Deep-copied first: the tree is a module-level constant in the plugin
        package, and a request that filled it in place would leak its values into
        the next one.
        """
        return translate_display(copy.deepcopy(self.settings))

    def translated_actions(self) -> list:
        return translate_display(copy.deepcopy(self.actions))

    def translated_info(self) -> dict:
        return translate_display(
            {
                "name": self.name,
                "description": self.description,
                "version": self.version,
                "author": self.author,
                "type": self.type,
            }
        )

    # -- stored settings ------------------------------------------------

    def get_plugin_settings(self) -> dict:
        """This plugin's saved settings, or ``{}``.

        Always a dict, including on an instance with no `active_plugins` document.
        """
        from archihub.infra.mongo import get_mongo

        record = get_mongo().get_record(
            "system", {"name": "active_plugins"}, fields={"plugins_settings": 1}
        )
        stored = (record or {}).get("plugins_settings") or {}
        value = stored.get(self.slug)
        return value if isinstance(value, dict) else {}

    def set_plugin_settings(self, settings: dict) -> None:
        """Replace this plugin's saved settings.

        Writes ONE key by dotted path rather than reading the whole
        `plugins_settings` map and writing it back, so two administrators saving
        different plugins' settings at the same time cannot discard each other's.
        """
        from archihub.infra.mongo import get_mongo

        if not isinstance(settings, dict):
            raise ValueError("Plugin settings must be an object")

        get_mongo().update_record_operator(
            "system",
            {"name": "active_plugins"},
            {"$set": {f"plugins_settings.{self.slug}": settings}},
        )
        logger.info("Settings updated for plugin %s", self.slug)

    # -- settings validation --------------------------------------------

    def validate_settings_fields(self, body: dict, group: str) -> str | None:
        """Check a submitted body against a settings group's declarations.

        Returns the error message, or ``None`` when it passes.
        """
        if group == "bulk" and not body.get("records") and not body.get("post_type"):
            # A bulk group is selected EITHER by an explicit record list or by a
            # content type. Both are real entry points: the processing screens
            # send `post_type`, and a plugin action on the record detail screen
            # sends `records` for the one record being looked at. Demanding
            # `post_type` unconditionally refuses every record-level action -
            # which the caller sees as a 400 with a message about content types
            # it never had the chance to choose.
            return _("No content type was specified")

        for setting in self.settings.get(f"settings_{group}") or []:
            if not setting.get("required"):
                continue
            key = setting.get("id")
            label = setting.get("label", key)
            if key not in body:
                # A DECLARED DEFAULT SATISFIES A REQUIRED FIELD. The two are not
                # in conflict: `required` means the run needs a value, and a
                # default is the plugin supplying one. Demanding it in the
                # payload as well makes the default unreachable and refuses
                # every caller that relied on it - including the record-detail
                # action, whose form the operator may never open.
                if "default" in setting:
                    continue
                return _("The field {label} is required", label=label)
            if setting.get("type") == "file" and not body.get(key):
                return _("The field {label} is required", label=label)
        return None

    # -- routes ---------------------------------------------------------

    def add_routes(self) -> None:
        """Override to declare this plugin's own routes on ``self.router``."""

    def settings_payload(self, kind: str) -> tuple[Any, int]:
        """What ``GET /settings/{kind}`` returns.

        Override to inject dynamic options — ``filesProcessing`` fills a content
        type picker, ``scheduleSystemTasks`` a list of registered Celery tasks.
        Overriding this replaces ~70 lines of duplicated route plumbing in each
        of them with the part that actually differs.
        """
        return self.select_settings(self.translated_settings(), kind)

    @staticmethod
    def select_settings(settings: dict, kind: str) -> tuple[Any, int]:
        """Pick one group out of a settings tree.

        ``all`` is the whole tree, ``settings`` the main group, anything else the
        group named ``settings_<kind>``. An unknown name is a 404.
        """
        if kind == "all":
            return settings, 200
        key = "settings" if kind == "settings" else f"settings_{kind}"
        if key not in settings:
            return {"msg": _("Setting not found")}, 404
        return settings[key], 200

    def save_settings(self, data: dict) -> tuple[dict, int]:
        """Handle ``POST /settings``. Override to validate before storing."""
        self.set_plugin_settings(data)
        return {"msg": _("Settings updated")}, 200

    def activate_settings(self) -> None:
        """Register this plugin's hooks. Called in EVERY process.

        Registering decides whether an event is *noticed*, not where the work
        runs: ``hooks.call`` turns a registered Celery task into a signature and
        sends it to the broker, so a worker still executes it. The web process
        is what raises nearly all of these events - creating a resource,
        attaching files, editing metadata - so a web process that skipped this
        would have an empty hook bus, and automatic processing would never
        start, with a 201 returned and nothing logged.
        """

    #: Set by a plugin that declares its own `/settings` routes. The shared
    #: pair is then skipped rather than registered alongside: Starlette matches
    #: in registration order, so a duplicate does not error - the plugin's own
    #: route answers and the framework's sits behind it, unreachable but
    #: published in the OpenAPI document, which is the one artefact nobody
    #: re-reads and every third-party integrator does.
    declares_own_settings_routes = False

    def build(self) -> APIRouter:
        """Assemble the router: the plugin's own routes, then the shared ones."""
        self.add_routes()
        self._add_image_route()
        if not self.declares_own_settings_routes:
            self._add_settings_routes()
        return self.router

    # -- the three every plugin gets ------------------------------------

    def _add_image_route(self) -> None:
        plugin = self

        @self.router.get("/image", responses={200: {"description": "Plugin icon"}})
        def get_image(
            current_user: CurrentUser = Depends(require_roles(*SETTINGS_ROLES)),
        ):
            """This plugin's icon, for the admin and processing screens."""
            from archihub.core.responses import file_response

            path = plugin.image_path()
            if path is None or not path.is_file():
                return json_response({"msg": _("Setting not found")}, 404)
            return file_response(path, media_type="image/png")

    def image_path(self):
        """``static/image.png`` beside the plugin package, if it has one."""
        from pathlib import Path

        if not self.module_file:
            return None
        return Path(self.module_file).resolve().parent / "static" / "image.png"

    def _add_settings_routes(self) -> None:
        plugin = self

        @self.router.get(
            "/settings/{kind}",
            responses={200: {"description": "Settings definition"}, 404: {"description": "Unknown group"}},
        )
        def get_settings(
            kind: str,
            current_user: CurrentUser = Depends(require_roles(*SETTINGS_ROLES)),
        ) -> JSONResponse:
            """This plugin's settings form definition, with current values."""
            payload, status_code = plugin.settings_payload(kind)
            return json_response(payload, status_code)

        @self.router.post(
            "/settings",
            responses={200: {"description": "Settings saved"}, 400: {"description": "Invalid settings"}},
        )
        def set_settings(
            data: str = Form(...),
            current_user: CurrentUser = Depends(require_roles(*SETTINGS_ROLES)),
        ) -> JSONResponse:
            """Save this plugin's settings.

            The body is `multipart/form-data` with a JSON string in `data`,
            which is what the settings screen sends. A client must not set
            `Content-Type` itself alongside a `FormData` body, or the boundary is lost.
            """
            try:
                parsed = json.loads(data)
            except (TypeError, ValueError):
                return json_response({"msg": _("Invalid settings payload")}, 400)

            if not isinstance(parsed, dict):
                return json_response({"msg": _("Invalid settings payload")}, 400)

            payload, status_code = plugin.save_settings(parsed)
            return json_response(payload, status_code)


# ---------------------------------------------------------------------------
# Helpers plugin routes share
# ---------------------------------------------------------------------------


def queue(task, task_name: str, user: str, result_type: str, *args, params: dict | None = None):
    """Dispatch a plugin task and record it against the user.

    Raises ``BrokerUnavailable`` if it cannot be queued, so a route answers 503
    rather than 201 with a task id that will never resolve.
    """
    from archihub.api.tasks.services import add_task

    try:
        queued = task.delay(*args)
    except Exception as exc:
        logger.exception("Could not queue %s", task_name)
        raise BrokerUnavailable(str(exc)) from exc

    try:
        add_task(queued.id, task_name, user, result_type, params=params or {})
    except Exception:
        logger.warning("Task %s queued but not recorded", queued.id)

    return queued


class BrokerUnavailable(RuntimeError):
    """The task queue could not accept the job."""


def task_result_file(task_id: str, user: str, *, is_admin: bool) -> tuple[Any, int]:
    """Resolve a completed task's downloadable result.

    Shared by ``liquidText``, ``inventoryMaker`` and ``massiveUpdater``, which
    each had their own near-identical copy of this — and each got a different
    subset of the checks right. Returns ``(path, 200)`` or ``(payload, status)``.

    THE PATH COMES OUT OF THE DATABASE AND IS RESOLVED, NOT CONCATENATED. The
    originals did ``USER_FILES_PATH + task['result']``, and `result` is whatever
    the task stored — so a task whose result began with `../` served a file from
    outside the user files root.
    """
    from pathlib import Path

    from archihub.core import files as filestore
    from archihub.core.settings import get_settings
    from archihub.infra.mongo import get_mongo

    task = get_mongo().get_record("tasks", {"taskId": task_id})
    if not task:
        return {"msg": _("Task does not exist")}, 404

    if task.get("user") != user and not is_admin:
        return {"msg": _("You do not have sufficient permissions")}, ROLE_FAILURE_STATUS

    status = task.get("status")
    if status == "pending":
        return {"msg": _("Task in process")}, 400
    if status != "completed":
        return {"msg": _("Failed task")}, 400
    if task.get("resultType") != "file_download":
        return {"msg": _("Task is not of type file_download")}, 400

    stored = task.get("result")
    if not isinstance(stored, str) or not stored:
        return {"msg": _("Failed task")}, 400

    try:
        path = filestore.resolve_within(get_settings().user_files_path, stored.lstrip("/"))
    except Exception:
        logger.error("Task %s result path escapes the user files root", task_id)
        return {"msg": _("Failed task")}, 400

    if not Path(path).is_file():
        return {"msg": _("File not found")}, 404

    return path, 200


def as_response(result: tuple[Any, int]) -> JSONResponse:
    payload, status_code = result
    return json_response(payload, status_code)


def object_ids(values: Iterable[Any], field: str) -> list:
    """Convert client-supplied ids, refusing the malformed rather than raising.

    Every plugin task did ``[ObjectId(x) for x in body['records']]`` inline, so
    one malformed id raised ``InvalidId`` inside a Celery task and the whole job
    was recorded as failed with a bson message.
    """
    from bson.errors import InvalidId
    from bson.objectid import ObjectId

    resolved = []
    for value in values or []:
        try:
            resolved.append(ObjectId(str(value)))
        except (InvalidId, TypeError):
            raise ValueError(_("Invalid identifier in {field}", field=field))
    return resolved


def require_list(body: dict, key: str) -> Sequence:
    value = body.get(key)
    if not isinstance(value, list):
        raise ValueError(_("You must specify a {field}", field=key))
    return value


__all__ = [
    "ArchiPlugin",
    "BrokerUnavailable",
    "SETTINGS_ROLES",
    "as_response",
    "object_ids",
    "queue",
    "require_list",
    "require_roles",
    "task_result_file",
    "translate_display",
]
