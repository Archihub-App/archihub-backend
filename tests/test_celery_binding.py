"""The web process must be able to queue work.

Task bodies use `@shared_task`, which resolves the Celery app at *call* time from
Celery's process-global default slot. Importing `archihub.worker.celery_app` is
what fills that slot. A process that never imports it gets a throwaway default
whose broker is `amqp://guest@localhost:5672//` - so `.delay()` fails with a
refused connection to a RabbitMQ this deployment does not run, and every queued
job is lost: automatic file processing on upload, reindexing, plugin bulk
actions.

**Why the binding is checked by reading `create_app`'s source rather than by
calling it.** Building the application reads the active plugin list from Mongo,
and this suite must run with no infrastructure (see `conftest.py`). Importing the
Celery app inside this test process would also make any "is it bound?" assertion
pass by its own doing, whatever `create_app` does. The regression that actually
happened is a missing import in one module, so that is what is asserted.
"""

from __future__ import annotations

import ast
import pathlib

from celery import current_app, shared_task

from archihub.core.settings import get_settings

APP_FACTORY = pathlib.Path(__file__).resolve().parent.parent / "archihub/core/app_factory.py"


def _imported_modules(source: str) -> set[str]:
    """Every module named in an `import x` / `from x import y`, at any depth."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_create_app_binds_the_celery_application():
    """The one line that makes the API able to queue anything at all."""
    imported = _imported_modules(APP_FACTORY.read_text())

    assert "archihub.worker.celery_app" in imported, (
        "archihub/core/app_factory.py must import archihub.worker.celery_app. "
        "Without it @shared_task resolves against Celery's default app, whose "
        "broker is amqp://guest@localhost:5672//, and every queued job is lost."
    )


def test_the_celery_module_claims_the_default_slot():
    """`set_default()` is what makes the import above sufficient."""
    source = (APP_FACTORY.parent.parent / "worker/celery_app.py").read_text()

    assert "celery_app.set_default()" in source


def test_the_configured_broker_is_not_celerys_amqp_default():
    from archihub.worker.celery_app import celery_app

    broker = celery_app.conf.broker_url

    assert broker == get_settings().celery_broker_url
    assert not str(broker).startswith("amqp"), (
        "Pointing at a RabbitMQ this deployment does not run reads as a broker "
        "outage rather than a misconfiguration."
    )


def test_a_shared_task_resolves_to_the_configured_broker():
    """The real dispatch path: a task declared the way plugin tasks are."""
    import archihub.worker.celery_app  # noqa: F401 - claims the default slot

    @shared_task(name="tests.binding_probe")
    def probe():  # pragma: no cover - resolved, never executed
        return None

    assert probe.app.conf.broker_url == get_settings().celery_broker_url


def test_the_broker_the_web_process_would_publish_to_is_reachable_in_principle():
    """Not that the broker is *up* - this suite runs with nothing running - but
    that the URL names a scheme this deployment actually uses. The bug produced
    `None`, which Celery silently reads as its AMQP default."""
    import archihub.worker.celery_app  # noqa: F401

    broker = current_app.conf.broker_url

    assert broker
    assert str(broker).startswith("redis://")


# Task-NAME registration is covered by `tests/test_worker_tasks.py`, against the
# worker's registry where it matters. The web process dispatches task *objects*
# it imported directly, so what it needs is the binding above, not the names.
