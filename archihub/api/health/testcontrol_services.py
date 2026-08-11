"""Test-control operations for disposable instances.

Port of ``app/api/health/testcontrol_services.py``. Reachable only through the
three-part gate in ``archihub/core/security/test_control.py``.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from archihub import __version__
from archihub.core.routing import iter_api_routes

logger = logging.getLogger(__name__)

SEED_VERSION = "1"


def get_status() -> tuple[dict, int]:
    return {
        "disposable": True,
        "app_version": __version__,
        "seed_version": SEED_VERSION,
    }, 200


def get_routes(app: FastAPI) -> tuple[dict, int]:
    """Live route inventory, for diffing against the OpenAPI spec.

    ``ArchiHUBTestRunner``'s ``swagger-inventory`` suite consumes this to prove
    no route escapes documentation. The legacy implementation walked Flask's
    ``app.url_map.iter_rules()``; the equivalent here is
    ``archihub.core.routing.iter_api_routes`` - see that module for why walking
    ``app.routes`` directly silently under-reports on modern FastAPI. The three
    keys are reproduced exactly:

        {"endpoint": <handler name>, "path": ..., "methods": [...]}

    ONE FORMAT CHANGE, deliberate: path parameters are rendered in OpenAPI style
    (``/users/{username}``) rather than Flask's converter syntax
    (``/users/<username>``), because that is what FastAPI puts in
    ``/openapi.json``. The suite compares this inventory against that spec, so
    matching it removes a normalisation step rather than adding one - but the
    runner's comparison logic should be checked when this backend goes live.
    """
    routes = [
        {
            "endpoint": route.name,
            "path": path,
            "methods": sorted(m for m in route.methods if m not in ("HEAD", "OPTIONS")),
        }
        for path, route in iter_api_routes(app)
    ]
    routes.sort(key=lambda item: (item["path"], item["methods"]))
    return {"routes": routes}, 200


# ---------------------------------------------------------------------------
# Reset / reseed
# ---------------------------------------------------------------------------
# The work itself is `archihub/worker/tasks/testcontrol.py` - read that module
# before changing anything here. These two functions only queue it and report on
# it; the destructive part runs in a worker and re-checks the disposability gate
# for itself, because a queued message outlives the request that created it.


def start_reset() -> tuple[dict, int]:
    """Queue a wipe-and-reseed, returning the id to poll.

    202, not 200: the reset has been accepted, not performed. Everything the
    caller needs to follow it - and, later, the generated administrator
    credentials - comes back through ``poll_reset``.
    """
    import uuid

    from archihub.api.tasks import services as tasks_services
    from archihub.worker.tasks.testcontrol import reset_task

    run_id = str(uuid.uuid4())

    try:
        task = reset_task.delay(run_id)
    except Exception:
        # The broker is down. Saying so is better than a task id that will
        # never resolve, which a runner would wait on until it timed out.
        logger.exception("Could not queue a test-control reset")
        return {"msg": "Could not queue the reset: the task queue is unavailable"}, 503

    try:
        tasks_services.add_task(task.id, "testcontrol.reset", "automatic", "msg")
    except Exception:
        # Bookkeeping only - the reset itself is already queued, and failing the
        # request here would leave it running with the caller told it had not.
        logger.warning("Reset %s queued but not recorded in the tasks collection", task.id)

    return {"task_id": task.id, "run_id": run_id}, 202


def poll_reset(task_id: str) -> tuple[dict, int]:
    """Whether a queued reset has finished, and what it produced.

    Always 200 - the *request* succeeded; the reset's own outcome is in the
    body's ``status``. That is the legacy contract and what the runner reads.
    """
    from archihub.api.tasks.services import _result

    result = _result(task_id)

    if result.state in ("PENDING", "STARTED", "RETRY"):
        return {"status": "pending"}, 200

    if result.state == "FAILURE":
        # The reset's failure reason IS returned here, unlike everywhere else in
        # this codebase. This endpoint exists only on an instance already marked
        # disposable, and a runner that cannot see why a reset failed cannot
        # report anything useful about the run that followed it.
        return {"status": "failed", "error": str(result.result)}, 200

    if result.successful():
        return {"status": "completed", "result": result.result}, 200

    return {"status": result.state.lower()}, 200
