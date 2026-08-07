"""FastAPI application construction.

Replaces ``app/__init__.py``'s ``create_app()``.

IMPORTANT - why conditional mounting happens here and NOT in a lifespan handler:
the legacy app decides at construction time which blueprints exist, by reading
the ``system`` collection (``api_activation`` for adminApi/publicApi,
``index_management`` for search) and ``active_plugins`` for the plugin routers.
A FastAPI ``lifespan`` runs *after* the application object and its routing table
are built, so routers added there are not reliably reflected in
``/openapi.json`` and each gunicorn worker would re-run the logic
independently. Anything that changes the route table therefore belongs in this
factory; ``lifespan`` is reserved for warming resources (connections, the
embedding model) that do not alter routing.

Only the health router is mounted so far - the remaining domains land through
Phase 3 (see PLAN_FASTAPI.md section 2).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from archihub import __version__
from archihub.core.errors import register_exception_handlers
from archihub.core.logging import RequestIdMiddleware, configure_logging
from archihub.core.settings import Settings, get_settings

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "This is the API documentation for "
    "[ArchiHub](https://www.instagram.com/archihub_app/). Additional information "
    "and general project documentation can be found "
    "[here](https://archihub-app.github.io/archihub.github.io/es/archihub/)."
    "<br /><br />Made with love in Colombia<br />"
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Warm shared resources on startup, release them on shutdown.

    Deliberately does NOT mount routers (see module docstring).
    """
    settings: Settings = app.state.settings
    logger.info(
        "ArchiHUB backend %s starting (environment=%s)",
        __version__,
        "DEV" if settings.is_dev else "PROD",
    )

    # NOTE: startup deliberately does NOT probe MongoDB (or any other backing
    # service) before accepting traffic.
    #
    # An earlier version pinged Mongo here. With the legacy connection string's
    # connectTimeoutMS=300000 that turned an unreachable database into a
    # multi-minute startup hang during which /health/live could not answer -
    # so an orchestrator could not tell "still booting" from "broken", and
    # would eventually kill a container that was merely waiting.
    #
    # Constructing a pymongo client does no I/O, so connections are established
    # lazily on first use, and /health/ready is what reports dependency state.
    # That is precisely the division of labour between a liveness and a
    # readiness probe.

    yield

    from archihub.infra.mongo import reset_mongo

    reset_mongo()
    logger.info("ArchiHUB backend stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    configure_logging(
        level="DEBUG" if settings.is_dev else "INFO",
        json_output=not settings.is_dev,
    )

    app = FastAPI(
        title="ARCHIHUB: A comprehensive tool for organizing and connecting information",
        version=__version__,
        description=DESCRIPTION,
        terms_of_service="https://archihub-app.github.io/archihub.github.io/es/conducta/",
        contact={
            "name": "BITSOL",
            "url": "https://bit-sol.com.co/",
            "email": "contact@bit-sol.com.co",
        },
        license_info={
            "name": "MIT",
            "url": "https://archihub-app.github.io/archihub.github.io/en/licencia/",
        },
        lifespan=lifespan,
        # Flasgger served the UI at /apidocs/ and the spec at /apispec_1.json.
        # Both paths are kept as aliases in main.py so existing operator
        # bookmarks and tooling do not break; these are the canonical ones.
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings

    _register_middleware(app, settings)
    register_exception_handlers(app)
    _register_routers(app)

    return app


def _register_middleware(app: FastAPI, settings: Settings) -> None:
    app.add_middleware(RequestIdMiddleware)

    # CORS, preserving the legacy asymmetry exactly.
    #
    # Flask-CORS was configured per-path: /adminApi/* and /publicApi/* always
    # allowed any origin, while everything else was restricted to URL_FRONTEND.
    # Starlette's CORSMiddleware has no per-path configuration, so the two rules
    # are unified to the *more permissive* of the pair only when the permissive
    # APIs are actually mounted. Those two blueprints are not ported yet, so
    # today this applies the restricted policy alone.
    #
    # The wildcard on the public/admin APIs is INTENTIONAL - those endpoints are
    # consumed by other organisations' scripts, not just this repo's frontend.
    # See the CORS note in CLAUDE.md before narrowing it.
    origins = settings.cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins if isinstance(origins, list) else ["*"],
        allow_credentials=isinstance(origins, list),
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "X-Request-ID"],
    )


def _register_routers(app: FastAPI) -> None:
    # NOTE: use archihub.core.routing.include_router, never app.include_router
    # directly - the wrapper keeps the registry that /health/test-control/routes
    # reads. See that module for why app.routes cannot be walked instead.
    from archihub.api.health.router import router as health_router
    from archihub.api.health.router import test_control_router
    from archihub.core.routing import include_router

    include_router(app, health_router)

    # Always mounted, exactly like the legacy blueprint. These routes exist on
    # every instance; it is the per-request dependency that 404s them when the
    # instance is not disposable - not their absence from the routing table.
    include_router(app, test_control_router)
