"""Mounting active plugins onto the application.

Replaces ``app/__init__.py:register_plugin()``, which did:

```python
plugin_module = __import__(f'app.plugins.{plugin_name}', fromlist=[...])
plugin_bp = plugin_module.ExtendedPluginClass(plugin_name, __name__, **plugin_module.plugin_info)
plugin_bp.add_routes()
plugin_bp.get_image()
plugin_bp.get_settings()
if os.environ.get('CELERY_WORKER', False):
    plugin_bp.activate_settings()
app.register_blueprint(plugin_bp, url_prefix=f'/{plugin_url_prefix}')
```

Two behavioural changes, both deliberate.

**One broken plugin must not take the instance down.** Letting an exception from
a plugin's construction propagate out of ``create_app`` means one plugin's
missing dependency denies access to the entire archive, every unrelated route
included. Here a plugin that fails to build is logged loudly and skipped, and
its absence is recorded so ``/system/plugins`` can report it. The decision that
genuinely should refuse startup — an active plugin this backend cannot support
at all — runs earlier, in the app factory.

**A plugin is built once and reused.** ``get_mounted()`` returns the same
instances the router was built from, so a settings read and a route handler
cannot disagree about what the plugin is. Constructing a fresh instance per call
- per mount, per settings request, per beat tick, inside each task body - makes
that disagreement possible and expensive.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

logger = logging.getLogger(__name__)

#: slug -> ArchiPlugin, for everything that mounted successfully.
_mounted: dict = {}

#: slug -> reason, for everything that did not.
_failed: dict[str, str] = {}


def build_plugin(slug: str):
    """Import a plugin package and construct its ``ArchiPlugin``.

    A plugin package exposes ``plugin_info`` and ``build()``; ``build()`` returns
    the ``ArchiPlugin``. Keeping construction in the package rather than
    reflecting over a class name means a plugin can decide how it is put
    together without this module knowing.
    """
    from archihub.plugins.framework.discovery import PluginDiscoveryError, import_plugin

    module = import_plugin(slug)
    factory = getattr(module, "build", None)
    if not callable(factory):
        raise PluginDiscoveryError(
            f"Plugin {slug!r} exposes no build() function; see "
            "archihub/plugins/framework/base.py for what a plugin package must provide"
        )
    return factory()


def mount_plugins(app: FastAPI, slugs: list[str] | None = None) -> dict:
    """Build and mount every active, ported plugin. Returns what mounted."""
    from archihub.core.routing import include_router
    from archihub.plugins.framework.discovery import get_active_plugin_slugs
    from archihub.plugins.framework.ported_registry import is_ported

    _mounted.clear()
    _failed.clear()

    if slugs is None:
        try:
            slugs = get_active_plugin_slugs()
        except Exception:
            logger.exception("Could not read the active plugin list; mounting none")
            return {}

    for slug in slugs:
        if not is_ported(slug):
            # Not an error here: the startup guard has already decided whether
            # this instance may run at all, and in bypass mode it deliberately
            # may. Logged there, once, rather than again per plugin.
            continue

        try:
            plugin = build_plugin(slug)
            include_router(app, plugin.build())
        except Exception as exc:
            logger.exception("Plugin %s could not be mounted; continuing without it", slug)
            _failed[slug] = str(exc)
            continue

        _mounted[slug] = plugin
        logger.info("Mounted plugin %s at /%s", slug, slug)

    return dict(_mounted)


def get_mounted() -> dict:
    """The plugins mounted on this process, by slug."""
    return dict(_mounted)


def get_failed() -> dict[str, str]:
    """Plugins that were active and ported but failed to build, with why."""
    return dict(_failed)


def get_plugin(slug: str):
    """One mounted plugin, or ``None``."""
    return _mounted.get(slug)


def activate_plugin_settings() -> None:
    """Run every mounted plugin's ``activate_settings``. EVERY process.

    This is what registers the hooks that fire plugin tasks on resource and file
    events, and it has to happen wherever those events are raised. The web
    process raises most of them - creating a resource, attaching files, editing
    metadata - so a web process with an empty hook bus is one where automatic
    processing never runs, with nothing logged and a 201 returned.

    Registering here does not mean the work happens here. A registered Celery
    task is turned into a signature and sent to the broker; a worker executes
    it. Only genuinely synchronous hooks run inline, and those (field validation
    and rendering) are part of the request's own path by design.

    The legacy code reached the same place by an easily-missed route: the mount
    helper called this when ``CELERY_WORKER`` was set, and each plugin's
    ``__init__`` called it when that variable was NOT set.
    """
    for slug, plugin in _mounted.items():
        try:
            plugin.activate_settings()
        except Exception:
            logger.exception("Plugin %s failed to register its hooks", slug)


def system_actions(placement: str) -> list[dict]:
    """Every mounted plugin's actions for one UI placement.

    Each action is tagged with the plugin that owns it, which is what the
    frontend uses to build the request URL.
    """
    actions: list[dict] = []
    for slug, plugin in _mounted.items():
        for action in plugin.translated_actions() or []:
            if action.get("placement") == placement:
                actions.append({**action, "plugin": slug})
    return actions
