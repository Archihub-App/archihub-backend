"""Which plugins have been ported to FastAPI.

WHY THIS FILE EXISTS (PLAN_FASTAPI.md decision 5)

All 24 plugin directories under ``app/plugins/`` subclass Flask's ``Blueprint``,
and 20 of them define Celery tasks. Only five are in scope for this migration:
filesProcessing, inventoryMaker, liquidText, massiveUpdater and
scheduleSystemTasks.

A default fresh install seeds only the base plugins, so in development
everything looks fine. But ArchiHUB is self-hosted by multiple independent
organisations, and an existing instance may well have ``ocrProcessing``,
``transcribeWhisperX``, ``mqttHandler`` or ``documentInsideSearch`` listed in its
``active_plugins`` record. Booting the FastAPI stack against such an instance
would try to import a Flask Blueprint into a process with no Flask app - an
opaque crash at startup, in production, on someone else's server.

So startup refuses to proceed, with a message naming the offending plugin and
what to do about it. Both the web process and the Celery worker enforce it: a
worker that started while the web process refused would run tasks against a
half-configured instance.

MAINTENANCE: add a slug here only once the plugin genuinely runs on the new
stack - its routes, its Celery tasks and its hook registrations. This list is
the difference between a controlled refusal and a production incident.
"""

from __future__ import annotations

# Ported and verified against the FastAPI stack (Phase 5).
PORTED_PLUGINS: frozenset[str] = frozenset(
    {
        "filesProcessing",
        "inventoryMaker",
        "liquidText",
        "massiveUpdater",
        "scheduleSystemTasks",
    }
)

# In scope for this migration but not finished yet. Listed separately purely so
# the error message can distinguish "coming in this project" from "not planned",
# which tells an operator whether to wait or to deactivate. Empty now that all
# five are done; kept because the distinction is what the message is built on,
# and the next plugin to be scheduled goes here first.
IN_SCOPE_PLUGINS: frozenset[str] = frozenset()


class UnportedPluginError(RuntimeError):
    """Raised at startup when an active plugin has not been ported."""


def is_ported(slug: str) -> bool:
    return slug in PORTED_PLUGINS


def check_active_plugins(active_slugs: list[str]) -> None:
    """Refuse to start when any active plugin is not yet ported.

    Called from both the ASGI app factory and the Celery app.
    """
    unported = [slug for slug in active_slugs if slug not in PORTED_PLUGINS]
    if not unported:
        return

    in_scope = sorted(s for s in unported if s in IN_SCOPE_PLUGINS)
    out_of_scope = sorted(s for s in unported if s not in IN_SCOPE_PLUGINS)

    lines = [
        "Refusing to start: active plugins have not been ported to the FastAPI backend.",
        "",
        "These plugins are still Flask Blueprints and cannot be mounted here. Starting",
        "anyway would fail later with an opaque import error, or - worse - appear to",
        "start while silently serving an instance whose features are missing.",
        "",
    ]
    if in_scope:
        lines += [
            f"  Being ported in this migration ({len(in_scope)}): {', '.join(in_scope)}",
            "    -> wait for that phase, or run the legacy backend meanwhile.",
            "",
        ]
    if out_of_scope:
        lines += [
            f"  Not part of this migration ({len(out_of_scope)}): {', '.join(out_of_scope)}",
            "    -> they must be ported, or deactivated on this instance, before cutover.",
            "",
        ]
    lines += [
        "To deactivate a plugin, remove its slug from the `data` array of the",
        "`active_plugins` document in the `system` MongoDB collection.",
        "",
        "To bypass this check while developing against a disposable instance only,",
        "set ARCHIHUB_ALLOW_UNPORTED_PLUGINS=true. Never set it on a real deployment:",
        "the routes and scheduled tasks those plugins provide will simply not exist.",
    ]
    raise UnportedPluginError("\n".join(lines))
