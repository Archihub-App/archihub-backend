"""ArchiHUB backend (FastAPI).

The ArchiHUB backend: a FastAPI application over MongoDB, Elasticsearch,
Qdrant, Redis and Celery, with a plugin framework for the processing pipelines
an archive configures for itself.

ARCHITECTURE NOTE - this stack is intentionally synchronous.
Route handlers are declared with plain ``def`` (not ``async def``), which makes
Starlette run them in a worker threadpool. That is a deliberate decision, not an
oversight: HTTP routes and Celery tasks call the *same* service functions and
share the *same* client singletons, so a split async-web/sync-worker design
would require every service module to bind its client to two different types at
once.
"""

__version__ = "2.0.0"
