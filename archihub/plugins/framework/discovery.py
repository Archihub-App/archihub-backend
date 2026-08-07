"""Plugin discovery: one implementation, used by both processes.

The legacy tree discovers plugins TWICE, in two hand-maintained copies:

* ``app/__init__.py:register_plugin()`` - imports ``app.plugins.<slug>``, builds
  the Blueprint, mounts it on Flask.
* ``app/celery_schedule.py:build_plugin_beat_schedule()`` - imports
  ``app.plugins.<slug>`` again, independently, to read ``plugin_info`` for the
  beat schedule.

Two copies of "how do I find and load a plugin" drift. They already disagree on
error handling: the scheduler catches import failures and skips the plugin,
while the web process lets them crash startup. Collapsed into one module here,
imported by both the ASGI app factory and the Celery app.

SECURITY NOTE: plugin slugs come from the ``active_plugins`` document in MongoDB
and are interpolated into an import path, so they are validated against a strict
pattern before use - see :func:`_validate_slug`. Writing to that document already
requires high privilege, but that is not a reason to leave an arbitrary-import
primitive in place: defence in depth means the second control still holds when
the first one fails.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
from types import ModuleType
from typing import Any

from archihub.plugins.framework.ported_registry import check_active_plugins

logger = logging.getLogger(__name__)

PLUGIN_PACKAGE = "archihub.plugins"
# Legacy location, referenced only in messages - deliberately never imported.
LEGACY_PLUGIN_PACKAGE = "app.plugins"

# A plugin slug is a directory name: letters, digits, underscore, hyphen. No
# dots (which would traverse packages) and no path separators.
_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class PluginDiscoveryError(RuntimeError):
    """Raised when a plugin cannot be located or loaded."""


def _validate_slug(slug: Any) -> str:
    if not isinstance(slug, str) or not _SLUG_PATTERN.match(slug):
        raise PluginDiscoveryError(
            f"Invalid plugin slug {slug!r} in the active_plugins document. "
            "Slugs must match [A-Za-z0-9_-]+ - a value containing dots or slashes "
            "could be used to import arbitrary modules."
        )
    return slug


def get_active_plugin_slugs(mongo: Any | None = None) -> list[str]:
    """Read the active plugin list from the ``system`` collection.

    Returns validated slugs. An entry that fails validation is dropped with a
    warning rather than aborting the whole list, so one malformed row cannot
    take an instance down - but it is never imported.
    """
    if mongo is None:
        from archihub.infra.mongo import get_mongo

        mongo = get_mongo()

    record = mongo.get_record("system", {"name": "active_plugins"}, fields={"data": 1}) or {}
    slugs: list[str] = []
    for raw in record.get("data") or []:
        try:
            slugs.append(_validate_slug(raw))
        except PluginDiscoveryError as exc:
            logger.error("Skipping plugin entry: %s", exc)
    return slugs


def unported_plugins_allowed() -> bool:
    return os.environ.get("ARCHIHUB_ALLOW_UNPORTED_PLUGINS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def assert_active_plugins_are_ported(mongo: Any | None = None) -> list[str]:
    """Enforce the cutover guard. Returns the active slugs when they all pass.

    Called by the ASGI app factory AND the Celery app - see
    ``ported_registry.py`` for why both.
    """
    if unported_plugins_allowed():
        # Bypass mode must never block startup, including when there is no
        # database to ask - it is used in tests and in local development.
        try:
            slugs = get_active_plugin_slugs(mongo)
        except Exception as exc:
            logger.debug("Plugin check skipped (bypass set, settings unreadable): %s", exc)
            return []

        from archihub.plugins.framework.ported_registry import PORTED_PLUGINS

        skipped = [s for s in slugs if s not in PORTED_PLUGINS]
        if skipped:
            logger.warning(
                "ARCHIHUB_ALLOW_UNPORTED_PLUGINS is set - starting WITHOUT these active "
                "plugins, whose routes and scheduled tasks will not exist: %s",
                ", ".join(sorted(skipped)),
            )
        return slugs

    try:
        slugs = get_active_plugin_slugs(mongo)
    except Exception as exc:
        # An unreachable database is a DIFFERENT failure from a plugin that is
        # not ported, and conflating them sends an operator hunting the wrong
        # problem. Both are fatal - the application cannot serve a single route
        # without MongoDB, so coming up to emit 500s helps nobody - but the
        # message has to say which one happened.
        raise PluginDiscoveryError(
            "Refusing to start: could not read the active plugin list from MongoDB, "
            "so it is not possible to verify that every active plugin is supported "
            f"by this backend. Underlying error: {exc}"
        ) from exc

    check_active_plugins(slugs)
    return slugs


def import_plugin(slug: str) -> ModuleType:
    """Import a ported plugin package by slug.

    DELIBERATELY LOOKS ONLY IN ``archihub.plugins``. It does not fall back to the
    legacy ``app.plugins`` location, even though that is where the not-yet-ported
    plugins still live.

    The reason is that ``app.plugins.<slug>`` is a subpackage of ``app``, so
    importing one executes ``app/__init__.py`` - which builds and boots the
    entire Flask application at module scope: ``torch`` import and monkeypatch,
    Mongo reads, SkillManager start, plugin registration, a Babel instance, the
    runtime-restart monitor thread. Reading one metadata dictionary would drag a
    second, fully-initialised web framework into the Celery worker or the ASGI
    process.

    An earlier version did fall back, and beat duly logged four
    "missing dependency 'torch'" warnings on startup - the visible tip of it
    having tried to boot Flask inside the beat process.

    Unported plugins are blocked at startup by the allowlist guard anyway, so
    nothing legitimately needs their metadata here.
    """
    _validate_slug(slug)

    try:
        return importlib.import_module(f"{PLUGIN_PACKAGE}.{slug}")
    except ModuleNotFoundError as exc:
        if exc.name == f"{PLUGIN_PACKAGE}.{slug}":
            raise PluginDiscoveryError(
                f"Plugin {slug!r} is not available in {PLUGIN_PACKAGE} "
                "(not ported to the FastAPI backend yet)"
            ) from exc
        # A ModuleNotFoundError from one of the plugin's OWN imports is a real
        # failure - a missing dependency - and must not be reported as "plugin
        # not found", which would send someone looking in the wrong place.
        raise PluginDiscoveryError(
            f"Plugin {slug!r} failed to import: missing dependency {exc.name!r}"
        ) from exc


def get_plugin_info(slug: str) -> dict:
    """Return a plugin's ``plugin_info`` metadata, or ``{}`` if unavailable.

    Never raises: the beat scheduler calls this for every active plugin on a
    timer, and one broken plugin must not stop the others from being scheduled.
    """
    try:
        module = import_plugin(slug)
    except Exception as exc:
        logger.warning("Could not load plugin %s for metadata: %s", slug, exc)
        return {}

    info = getattr(module, "plugin_info", None)
    if not isinstance(info, dict):
        logger.warning("Plugin %s exposes no plugin_info dict", slug)
        return {}
    return info


def plugin_has_capability(slug: str, capability: str) -> bool:
    """Whether a plugin declares a capability (e.g. ``scheduler``)."""
    capabilities = get_plugin_info(slug).get("capabilities") or []
    return capability in capabilities
