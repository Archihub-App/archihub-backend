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

    if settings.auto_create_indexes:
        _ensure_indexes_safely()

    yield

    from archihub.infra.mongo import reset_mongo

    reset_mongo()
    logger.info("ArchiHUB backend stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    configure_logging(
        level="DEBUG" if settings.is_dev else "INFO",
        json_output=not settings.is_dev,
        access_log=settings.access_log_enabled,
    )

    # Bind the Celery application to this process before anything can dispatch.
    #
    # Task bodies are declared with `@shared_task`, which resolves the app at
    # CALL time from Celery's process-global default slot. Importing this module
    # is what fills that slot (`celery_app.set_default()`); until it is imported,
    # Celery answers `current_app` with a throwaway default whose broker is
    # `amqp://guest@localhost:5672//`.
    #
    # The web process never started a worker, so nothing else imported it - and
    # the failure was not a missing task but a REFUSED CONNECTION to a RabbitMQ
    # that this deployment does not run. Every job the API queued was lost:
    # automatic file processing on upload, reindexing, every plugin bulk action.
    # `tests/test_celery_binding.py` asserts the binding rather than trusting
    # import order, because this fails silently the moment it regresses.
    from archihub.worker.celery_app import celery_app  # noqa: F401

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

    # Fail closed BEFORE building the route table. An instance whose active
    # plugins are not all ported must not come up half-working: the routes and
    # scheduled tasks those plugins provide would simply be absent, which looks
    # like data loss to a user and is hard to attribute. See
    # archihub/plugins/framework/ported_registry.py (PLAN_FASTAPI.md decision 5).
    #
    # During the migration itself this is expected to trip - set
    # ARCHIHUB_ALLOW_UNPORTED_PLUGINS=true against a disposable instance to work
    # on the not-yet-ported parts of the stack.
    _check_plugin_readiness()

    _register_middleware(app, settings)
    register_exception_handlers(app)
    _register_routers(app)

    # After the plugins, so indexing sits above their automatic processing in
    # the hook order and builds its document from metadata they have finished
    # writing. See archihub/api/search/write_hooks.py.
    _register_index_hooks()

    return app


def _register_index_hooks() -> None:
    """Wire the search index into the resource write path. Never fatal."""
    from archihub.api.search.write_hooks import register_index_hooks

    try:
        register_index_hooks()
    except Exception:
        logger.exception("Could not register the indexing hooks; the search index will go stale")


def _ensure_indexes_safely() -> None:
    """Create missing MongoDB indexes, without ever blocking startup.

    Builds are backgrounded and idempotent, so this is cheap when the indexes
    already exist and safe on a populated production collection.

    Deliberately non-fatal: a missing index makes queries slow, while refusing to
    start denies service outright. It is logged loudly instead. Set
    AUTO_CREATE_INDEXES=false to manage indexes as an explicit migration step
    with tools/create_indexes.py.
    """
    try:
        from archihub.infra.indexes import ensure_indexes

        result = ensure_indexes()
        if result["created"]:
            logger.info("Created %d missing MongoDB index(es)", len(result["created"]))
        if result["failed"]:
            logger.error(
                "%d MongoDB index(es) could not be created; those queries will be "
                "unindexed until resolved: %s",
                len(result["failed"]),
                ", ".join(result["failed"]),
            )
    except Exception:
        logger.exception("Index check failed; continuing without it")


def _check_plugin_readiness() -> None:
    from archihub.plugins.framework.discovery import assert_active_plugins_are_ported

    try:
        assert_active_plugins_are_ported()
    except Exception as exc:
        # Reformat onto the log rather than letting a bare traceback carry it:
        # the message is a set of instructions for an operator, and a traceback
        # buries them.
        for line in str(exc).splitlines():
            logger.critical("%s", line)
        raise


def _register_middleware(app: FastAPI, settings: Settings) -> None:
    app.add_middleware(RequestIdMiddleware)

    # CORS, preserving the legacy asymmetry exactly.
    #
    # Flask-CORS was configured per-path: /adminApi/* and /publicApi/* always
    # allowed any origin, while everything else was restricted to URL_FRONTEND.
    # Starlette's CORSMiddleware has no per-path configuration, so the two rules
    # collapse into one.
    #
    # WHAT THAT MEANS IN PRACTICE. Both external APIs are now ported and are
    # registered on every instance (availability is a per-request question - see
    # api/external/router.py), so the permissive half always applies to some
    # route. With URL_FRONTEND unset this is `*` throughout, which is the legacy
    # behaviour for those two paths and MORE permissive than the legacy default
    # for the rest. Setting URL_FRONTEND restricts everything, including the two
    # APIs that other organisations' scripts call - so an operator who sets it
    # must add those callers' origins to it.
    #
    # Restoring the exact per-path split needs a small middleware of our own
    # rather than Starlette's; recorded here rather than done, because narrowing
    # CORS was tried once on the legacy stack and deliberately reverted (commit
    # 4b3e25d).
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


def _assert_public_routes_win(app: FastAPI) -> None:
    """Fail at startup if a parameterised route shadows a public one.

    A shadowed public route does not error - it authenticates, so an anonymous
    caller gets 401 where the contract promises a document. That is invisible in
    a route inventory and easy to reintroduce by moving one ``include_router``
    line, so it is checked here rather than trusted to ordering discipline.
    """
    from archihub.core.routing import iter_api_routes

    seen_parameterised: list[str] = []
    for path, route in iter_api_routes(app):
        if "/public" in path:
            for earlier in seen_parameterised:
                if _would_capture(earlier, path):
                    raise RuntimeError(
                        f"Route {earlier!r} is registered before {path!r} and would "
                        "capture it. Mount the public router first."
                    )
        elif "{" in path:
            seen_parameterised.append(path)


def _would_capture(parameterised: str, literal: str) -> bool:
    """Whether ``parameterised`` matches ``literal``'s shape segment for segment."""
    left, right = parameterised.strip("/").split("/"), literal.strip("/").split("/")
    if len(left) != len(right):
        return False
    return all(a.startswith("{") or a == b for a, b in zip(left, right))


def _register_routers(app: FastAPI) -> None:
    # NOTE: use archihub.core.routing.include_router, never app.include_router
    # directly - the wrapper keeps the registry that /health/test-control/routes
    # reads. See that module for why app.routes cannot be walked instead.
    from archihub.api.health.router import router as health_router
    from archihub.api.health.router import test_control_router
    from archihub.api.auth.router import router as auth_router
    from archihub.api.forms.router import router as forms_router
    from archihub.api.lists.router import router as lists_router
    from archihub.api.logs.router import router as logs_router
    from archihub.api.records.public_router import router as records_public_router
    from archihub.api.records.router import router as records_router
    from archihub.api.resources.public_router import router as resources_public_router
    from archihub.api.resources.router import router as resources_router
    from archihub.api.snaps.public_router import router as snaps_public_router
    from archihub.api.aiservices.router import router as aiservices_router
    from archihub.api.external.router import admin_router as admin_api_router
    from archihub.api.external.router import public_router as public_api_router
    from archihub.api.search.router import public_router as search_public_router
    from archihub.api.search.router import router as search_router
    from archihub.api.geosystem.router import router as geosystem_router
    from archihub.api.views.public_router import router as views_public_router
    from archihub.api.views.router import router as views_router
    from archihub.api.snaps.router import router as snaps_router
    from archihub.api.system.router import router as system_router
    from archihub.api.tasks.router import router as tasks_router
    from archihub.api.types.router import router as types_router
    from archihub.api.users.router import router as users_router
    from archihub.api.usertasks.router import router as usertasks_router
    from archihub.core.routing import include_router

    include_router(app, health_router)
    include_router(app, types_router)
    include_router(app, lists_router)
    include_router(app, forms_router)
    include_router(app, auth_router)
    include_router(app, users_router)
    include_router(app, system_router)
    include_router(app, logs_router)
    include_router(app, tasks_router)
    include_router(app, usertasks_router)
    include_router(app, resources_public_router)
    include_router(app, resources_router)

    # ORDER MATTERS, and not only for readability. The public record routes all
    # begin with the literal segment `public`, which `GET /records/{record_id}`
    # in the authenticated router would otherwise capture - Starlette matches in
    # registration order and takes the first route whose path converts. Mounted
    # first, then asserted below so a future reordering fails loudly instead of
    # quietly turning every public route into an authenticated 401.
    include_router(app, records_public_router)
    include_router(app, records_router)
    include_router(app, snaps_public_router)
    include_router(app, snaps_router)
    include_router(app, views_public_router)
    include_router(app, views_router)
    include_router(app, geosystem_router)
    include_router(app, aiservices_router)
    include_router(app, search_public_router)
    include_router(app, search_router)
    include_router(app, admin_api_router)
    include_router(app, public_api_router)
    _assert_public_routes_win(app)

    # Always mounted, exactly like the legacy blueprint. These routes exist on
    # every instance; it is the per-request dependency that 404s them when the
    # instance is not disposable - not their absence from the routing table.
    include_router(app, test_control_router)

    # Plugins LAST, so a plugin can never shadow a core route by declaring a
    # colliding path. Their prefixes are their own slugs, which makes a
    # collision unlikely rather than impossible - `views` is both a core domain
    # and a plausible plugin name.
    _mount_plugins(app)


def _mount_plugins(app: FastAPI) -> None:
    """Mount every active, ported plugin. Never fatal.

    The decision that CAN refuse startup - an active plugin this backend does
    not support at all - was already made in `_check_plugin_readiness`, before
    any route was built. By the time we get here the remaining failure is a
    plugin that is supported but broken, and taking the whole instance down for
    that means one plugin's missing dependency denies access to the archive.
    """
    from archihub.plugins.framework.mounting import activate_plugin_settings, mount_plugins

    try:
        mounted = mount_plugins(app)
    except Exception:
        logger.exception("Plugin mounting failed; continuing without plugins")
        return

    if mounted:
        logger.info("Mounted %d plugin(s): %s", len(mounted), ", ".join(sorted(mounted)))

    # THE WEB PROCESS NEEDS THE HOOKS TOO, and this is the half that was missing.
    #
    # The legacy code registered them from two places, which reads like a
    # duplication and is not: `register_plugin()` called `activate_settings()`
    # only when CELERY_WORKER was set, and the plugin's own `__init__` called it
    # only when CELERY_WORKER was NOT set. Between them every process got the
    # registrations; porting the first half alone left this one with an empty
    # hook bus.
    #
    # What that costs is not obvious from here, because dispatching is what a
    # registration is FOR: `hooks.call()` turns each registered Celery task into
    # a signature and sends it to the broker, so the job still runs in a worker.
    # With nothing registered, `resource_files_create` fires into an empty
    # registry - the upload succeeds, 201 comes back, and no derivative is ever
    # made. Synchronous hooks (`validate_field`, `resource_field`) are worse
    # still: they are part of the request's own path and simply do not happen.
    activate_plugin_settings()
