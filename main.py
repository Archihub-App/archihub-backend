"""ASGI entrypoint for the FastAPI backend.

Run with::

    uvicorn main:app --host 0.0.0.0 --port 5001                     # dev
    uvicorn main:app --host 0.0.0.0 --port 5001 --workers 4         # prod

NOT ``gunicorn -k uvicorn.workers.UvicornWorker``: that shim is deprecated and
has moved to the separate ``uvicorn-worker`` distribution, so depending on it
ties the deployment to something already on its way out. uvicorn runs its own
worker processes.

In a container this is started by ``start.sh``, which waits for Elasticsearch
and then supervises the server - it restarts uvicorn on SIGHUP, which is how a
restart requested from the admin screen takes effect.
"""

from __future__ import annotations

from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse

from archihub.core.app_factory import create_app

app = create_app()


# ---------------------------------------------------------------------------
# Aliases for the documentation URLs of ArchiHUB 1.x.
#
# /apidocs/ (the UI) and /apispec_1.json (the raw spec) appear in the docs site
# and in operator bookmarks at every deployment, so they keep working.
# ---------------------------------------------------------------------------


@app.get("/apidocs/", include_in_schema=False)
@app.get("/apidocs", include_in_schema=False)
def swagger_ui_alias():
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Swagger UI")


@app.get("/apispec_1.json", include_in_schema=False)
def openapi_spec_alias() -> JSONResponse:
    return JSONResponse(app.openapi())
