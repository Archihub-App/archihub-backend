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
# NOT YET PORTED. The reset flow wipes Mongo/Elasticsearch/Qdrant/Redis and then
# reseeds through the onboarding path (`set_system_setting` + `set_first_time`
# in app/api/system/services.py). Both of those live in the `system` domain,
# which is ported in Phase 3 step 3, and the wipe runs as a Celery task, wired
# in Phase 4.
#
# Until then these return 503 rather than silently appearing to work: a
# test-control reset that no-ops would leave a suite running against stale data
# and reporting green. During Phase 0-2 the diff harness seeds state through the
# LEGACY backend's reset endpoint instead - both stacks share one database, so
# that is sufficient. See tools/diff_harness.py.

_NOT_PORTED_MSG = (
    "Reset is not available on the FastAPI backend yet (ported in Phase 3/4). "
    "Use the legacy backend's /health/test-control/reset against the same database."
)


def start_reset() -> tuple[dict, int]:
    logger.warning("test-control reset requested but not yet ported")
    return {"msg": _NOT_PORTED_MSG}, 503


def poll_reset(task_id: str) -> tuple[dict, int]:
    logger.warning("test-control reset poll requested but not yet ported")
    return {"msg": _NOT_PORTED_MSG}, 503
