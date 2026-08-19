"""The Celery application.

In the legacy code the Celery app did not exist as a module: it was built inside
``app/__init__.py``'s ``celery_init_app(app)`` and hung off the Flask object as
``app.celery_app``, so anything wanting the task queue had to import - and boot -
the entire web application first.

Here it is a standalone module that imports no web framework at all. That is
what lets ``scheduleSystemTasks`` ask the broker which tasks are registered
(``celery_app.control.inspect()``) without Flask's ``current_app`` indirection,
and what lets a worker start without constructing an ASGI app.

The other thing that disappears is ``FlaskTask``, whose ``__call__`` wrapped
*every* task execution in ``with app.app_context():``. All 39 task bodies relied
on that implicitly - none of them acquired a context themselves - and it is what
made ``flask_babel`` work inside a worker. Since the replacement translator
(``archihub.core.i18n``) resolves an instance-wide setting straight from Mongo,
task bodies need no ambient context at all; they just call ``_()``.

Run with::

    celery --app archihub.worker.celery_app worker
    celery --app archihub.worker.celery_app beat
"""

from __future__ import annotations

import logging
import sys

from celery import Celery
from celery.signals import celeryd_init, worker_process_init

from archihub.core.settings import get_settings

settings = get_settings()

celery_app = Celery("archihub")

celery_app.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_broker_url,
    broker_connection_retry_on_startup=True,
    # Legacy set task_ignore_result=True globally and then overrode it with
    # ignore_result=False on essentially every task. Results are polled through
    # AsyncResult by app/api/tasks, so keep them.
    task_ignore_result=False,
    enable_utc=False,
    timezone="America/Bogota",
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    broker_transport_options={"visibility_timeout": 43200},
    # 12h ceilings: transcription, OCR and media processing genuinely run long.
    task_time_limit=43200,
    task_soft_time_limit=43000,
    beat_max_loop_interval=settings.celery_beat_refresh_interval,
)

# macOS cannot fork the default pool safely; legacy applied the same fallback.
_worker_pool = settings.celery_worker_pool or ("solo" if sys.platform == "darwin" else None)
if _worker_pool:
    celery_app.conf.worker_pool = _worker_pool
celery_app.conf.worker_concurrency = 1 if _worker_pool == "solo" else settings.celeryd_concurrency

# Hot-reloading beat scheduler: re-reads plugin schedules from MongoDB so a
# settings change takes effect without restarting beat. See worker/scheduler.py.
celery_app.conf.beat_scheduler = "archihub.worker.scheduler:MongoPluginScheduler"

# Claim Celery's process-global default app slot. Our own code always passes the
# app explicitly (see api/tasks/services._result), but plugin and third-party
# code commonly constructs a bare `AsyncResult(task_id)`, which resolves against
# this default - and an unclaimed default carries a DisabledBackend whose every
# state query raises.
celery_app.set_default()

# Task modules. `autodiscover_tasks` imports `<package>.<related_name>`, so this
# resolves to `archihub.worker.tasks` - the package whose `__init__` imports each
# task module and therefore runs every `@shared_task` decorator. Pointing it at
# `archihub.worker.tasks` instead would look for a `tasks.tasks` submodule that
# does not exist, and the worker would start with nothing registered.
celery_app.autodiscover_tasks(["archihub.worker"], force=False)


@celeryd_init.connect
def _init_worker(**_kwargs) -> None:
    """Prepare the worker's MAIN process, before it forks or consumes anything.

    Three jobs, and the order is the point.

    1. Enforce the unported-plugin guard here as well as in the web process. A
       worker that started while the web process refused would execute tasks
       against an instance whose configuration the application has rejected.
    2. Configure logging, so task output keeps the structured format and the
       correlation ids rather than falling back to Celery's default.
    3. **Build the plugins, which is what registers their Celery tasks.**

    THE THIRD ONE MUST HAPPEN IN THE PARENT, and it is worth being explicit
    about why. Celery's consumer runs in the main process: it looks each
    incoming message's task name up in ``app.tasks`` and dispatches to a child
    only if it finds one. A name the parent does not know is answered
    ``NotRegistered`` and the message is dropped - **before any child sees it**.

    An earlier version of this hooked ``worker_process_init``, which fires per
    forked child. The nine plugin tasks were then absent from the parent's
    registry, so every plugin job would have been discarded on arrival while the
    worker looked perfectly healthy - it would even have reported ``ready``. The
    symptom is the startup banner: ``[tasks]`` listed six names instead of
    fifteen.
    """
    from archihub.core.logging import configure_logging

    configure_logging(level="DEBUG" if settings.is_dev else "INFO", json_output=not settings.is_dev)

    try:
        from archihub.plugins.framework.discovery import assert_active_plugins_are_ported

        assert_active_plugins_are_ported()
    except Exception:
        # Log before re-raising: Celery's own startup error reporting is terse,
        # and the guard's message is the actionable part.
        logging.getLogger(__name__).critical(
            "Worker refusing to start - see the plugin guard message below", exc_info=True
        )
        raise

    _load_plugins()


@worker_process_init.connect
def _init_worker_process(**_kwargs) -> None:
    """Prepare a freshly forked child.

    Re-runs the plugin load rather than trusting inheritance. Under the default
    prefork pool a child inherits the parent's module state and this is a no-op;
    under a ``spawn`` start method it inherits nothing, and without this the
    child would have no hook registrations - so automatic file processing would
    silently never fire. Both ``mount_plugins`` and ``hooks.register`` are
    idempotent, which is what makes running it twice safe.
    """
    _load_plugins()


def _load_plugins() -> None:
    """Build the active plugins and register their hooks. Never fatal.

    Building is what imports each plugin module and therefore what runs its
    ``@shared_task`` decorators. ``activate_settings`` then registers the hooks
    that fire those tasks on resource and file events - worker-side only,
    because registering them in the web process would dispatch the chains from
    whichever process happened to serve the request.
    """
    try:
        from archihub.plugins.framework.mounting import (
            activate_plugin_settings,
            mount_plugins,
        )

        mount_plugins(_NoRouterApp())
        activate_plugin_settings()

        # The worker fires `resource_update` too - `plugins.framework.data`
        # does it whenever a plugin task writes back to a resource - so it needs
        # the indexing registrations as much as the web process does. The legacy
        # `create_app` ran in both, which is what gave the worker them.
        from archihub.api.search.write_hooks import register_index_hooks

        register_index_hooks()
    except Exception:
        logging.getLogger(__name__).exception(
            "Could not load plugins; their tasks and automatic processing will not run"
        )


class _NoRouterApp:
    """Stands in for the FastAPI app when mounting inside a worker.

    ``mount_plugins`` builds each plugin and then includes its router. A worker
    has no router table, but building the plugin is what imports its module and
    therefore what registers its Celery tasks - so the build has to happen, and
    only the include is a no-op here.
    """

    def __init__(self) -> None:
        from types import SimpleNamespace

        self.routes: list = []
        # `core.routing.include_router` records what it mounted on app.state.
        self.state = SimpleNamespace()

    def include_router(self, *args, **kwargs) -> None:
        return None
