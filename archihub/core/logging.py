"""Application logging.

The legacy codebase has 274 bare ``print()`` calls and imports ``logging`` in
only five infra-adjacent modules - never in a ``routes.py`` or ``services.py``.
Diagnosing a production incident therefore meant reading container stdout with
no levels, no timestamps and no way to filter by subsystem.

This module sets up stdlib logging once at startup:

* JSON lines in production (parseable by whatever the operator runs), plain
  human-readable lines in DEV.
* A request id, generated per request or taken from an inbound ``X-Request-ID``,
  bound to a ``contextvar`` and injected into every record - so all log lines
  from one request can be correlated. Celery tasks bind their task id the same
  way.

Not to be confused with ``app/api/logs/`` - that is the Mongo-backed **audit**
log (``register_log``, a 29-action enum, field-level diffs for resource history).
It is a product feature, stays a plain function call at the point where business
logic decides an action succeeded, and is unrelated to this module.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.config
import sys
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "archihub_request_id", default=None
)


def get_request_id() -> str | None:
    return request_id_var.get()


class RequestIdFilter(logging.Filter):
    """Attach the current request/task id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Install handlers. Safe to call more than once (idempotent per process)."""
    formatter: logging.Formatter
    if json_output:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # These are chatty at INFO and drown out application logs.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Bind a request id for the duration of each request and echo it back."""

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def bind_task_id(task_id: str | None) -> None:
    """Bind a Celery task id as the correlation id inside a worker."""
    request_id_var.set(task_id)
