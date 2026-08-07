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

import sys

from celery import Celery

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

# NOTE: task modules and the Mongo-backed beat scheduler are wired in Phase 2/4
# (see PLAN_FASTAPI.md section 2). Until then this app intentionally has an
# empty task registry - enough for `celery ... worker` to boot and answer the
# control-plane ping that /health/ready issues.
