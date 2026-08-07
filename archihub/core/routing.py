"""Router registration and live-route introspection.

Exists because of a genuine trap discovered while porting
``/health/test-control/routes``.

That endpoint exists so ``ArchiHUBTestRunner``'s ``swagger-inventory`` suite can
diff the routes the application *actually serves* against the routes the
generated OpenAPI spec *documents*, and fail when one drifts from the other. The
inventory must therefore be derived independently of the spec - deriving it from
``app.openapi()`` would make the comparison compare the spec to itself and pass
unconditionally.

The obvious implementation, walking ``app.routes`` for ``APIRoute`` instances,
is wrong on modern FastAPI: since 0.13x, ``include_router`` does not flatten the
child routes into ``app.routes``. It appends a single private
``fastapi.routing._IncludedRouter`` wrapper per call, whose children are only
reachable through undocumented internals. A naive walk returns just the handful
of routes declared with ``@app.get`` directly - so the inventory would look
almost empty, every real route would appear "missing", and the suite would
either fail confusingly or (if it only checks the other direction) pass while
verifying nothing.

So registration goes through :func:`include_router` here, which records what was
mounted. Enumeration then uses ``APIRouter.routes`` - public, stable API - rather
than reverse-engineering the wrapper.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

_REGISTRY_ATTR = "archihub_mounted_routers"


def include_router(app: FastAPI, router: APIRouter, **kwargs: object) -> None:
    """Mount ``router`` on ``app`` and remember it for introspection.

    Use this instead of ``app.include_router`` so the route inventory stays
    complete. ``kwargs`` is forwarded verbatim.
    """
    app.include_router(router, **kwargs)

    registry: list[tuple[APIRouter, str]] = getattr(app.state, _REGISTRY_ATTR, None) or []
    registry.append((router, str(kwargs.get("prefix", ""))))
    setattr(app.state, _REGISTRY_ATTR, registry)


def iter_api_routes(app: FastAPI) -> list[tuple[str, APIRoute]]:
    """Yield ``(full_path, route)`` for every documented route the app serves.

    Routes marked ``include_in_schema=False`` are excluded: they are
    intentionally absent from the OpenAPI spec (the ``/apidocs`` and
    ``/apispec_1.json`` compatibility aliases), so including them here would
    make the inventory-vs-spec diff report false drift.
    """
    found: list[tuple[str, APIRoute]] = []
    seen: set[tuple[str, frozenset[str]]] = set()

    def _add(prefix: str, route: APIRoute) -> None:
        if not route.include_in_schema:
            return
        # An APIRouter applies its own `prefix` when the route is declared, so
        # `route.path` already carries it; `extra_prefix` covers the case where
        # include_router() was additionally given prefix=...
        full_path = f"{prefix}{route.path}"
        key = (full_path, frozenset(route.methods or ()))
        if key in seen:
            return
        seen.add(key)
        found.append((full_path, route))

    for router, extra_prefix in getattr(app.state, _REGISTRY_ATTR, None) or []:
        for route in router.routes:
            if isinstance(route, APIRoute):
                _add(extra_prefix, route)

    # Routes declared straight on the app with @app.get/@app.post are still
    # flattened into app.routes, so pick those up too.
    for route in app.routes:
        if isinstance(route, APIRoute):
            _add("", route)

    return found
