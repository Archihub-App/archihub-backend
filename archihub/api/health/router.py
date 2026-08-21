"""Health and test-control routes.

The Flasgger YAML docstrings become FastAPI route metadata: prose moves to the
handler docstring (FastAPI reads it as the OpenAPI description), ``tags:`` moves
to the decorator, and the ``responses:`` map becomes the ``responses=`` kwarg.
The ``X-ArchiHUB-Test-Secret`` header parameter no longer needs documenting by
hand - it is derived from the dependency's signature.

Responses are returned through explicit ``JSONResponse`` objects because these
endpoints signal state through their status code (200 vs 503, 404 vs 403 vs 401)
and must not be reshaped by a response model. ``response_model`` stays off
until a route has been confirmed field-for-field, because it silently *filters*
undeclared fields - an invisible regression.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from archihub.api.health import services, testcontrol_services
from archihub.core.security.test_control import test_control_authenticate

router = APIRouter(prefix="/health", tags=["System health"])

# Every test-control route carries the same gate and the same failure modes.
_TEST_CONTROL_RESPONSES = {
    401: {"description": "Invalid or missing test secret"},
    403: {"description": "The instance is not marked as disposable"},
    404: {"description": "Test mode is not active"},
}


@router.get(
    "/live",
    responses={200: {"description": "The process is alive"}},
)
def live() -> JSONResponse:
    """Confirms that the process is running."""
    return JSONResponse(status_code=200, content={"alive": True})


@router.get(
    "/ready",
    responses={
        200: {"description": "All required dependencies are available"},
        503: {"description": "At least one dependency is unavailable"},
    },
)
def ready() -> JSONResponse:
    """Checks connectivity with MongoDB, Redis, Elasticsearch, Qdrant and Celery."""
    payload, status_code = services.get_readiness()
    return JSONResponse(status_code=status_code, content=payload)


# ---------------------------------------------------------------------------
# Test control - only functional on a disposable instance.
# See archihub/core/security/test_control.py for the gate.
# ---------------------------------------------------------------------------

test_control_router = APIRouter(
    prefix="/health/test-control",
    tags=["Test control"],
    dependencies=[Depends(test_control_authenticate)],
    responses=_TEST_CONTROL_RESPONSES,
)


@test_control_router.get("/status")
def test_control_status() -> JSONResponse:
    """Returns metadata about the disposable test instance."""
    payload, status_code = testcontrol_services.get_status()
    return JSONResponse(status_code=status_code, content=payload)


@test_control_router.get("/routes")
def test_control_routes(request: Request) -> JSONResponse:
    """Returns the live inventory of routes, to compare against the OpenAPI spec."""
    payload, status_code = testcontrol_services.get_routes(request.app)
    return JSONResponse(status_code=status_code, content=payload)


@test_control_router.post("/reset")
def test_control_reset() -> JSONResponse:
    """Resets the disposable instance: wipes the data and seeds a deterministic baseline."""
    payload, status_code = testcontrol_services.start_reset()
    return JSONResponse(status_code=status_code, content=payload)


@test_control_router.get("/reset/{task_id}")
def test_control_reset_status(task_id: str) -> JSONResponse:
    """Polls a reset task and, once complete, returns the generated admin credentials."""
    payload, status_code = testcontrol_services.poll_reset(task_id)
    return JSONResponse(status_code=status_code, content=payload)
