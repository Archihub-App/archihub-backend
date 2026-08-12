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
import time
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


def configure_logging(
    *, level: str = "INFO", json_output: bool = True, access_log: bool = True
) -> None:
    """Install handlers. Safe to call more than once (idempotent per process).

    ``access_log`` gates only the per-request line. Everything else - business
    errors, warnings, unhandled exceptions - is unaffected, because those are how
    an operator finds out something is wrong and are not verbosity.
    """
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

    # Uvicorn's own access log is silenced because `RequestIdMiddleware` emits a
    # richer line in its place - same information plus the correlation id and the
    # duration. Leaving both on prints every request twice.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # WARNING rather than removing the middleware: the access line is emitted
    # below every threshold this logger will pass, so nothing is computed and
    # nothing is printed, and turning it back on is a level change rather than a
    # different request path.
    logging.getLogger("archihub.access").setLevel(
        logging.NOTSET if access_log else logging.WARNING
    )


access_logger = logging.getLogger("archihub.access")

# Query keys whose value is never logged. Nothing in the API accepts a
# credential in the query string today; this keeps that true by default if one
# is ever added, because logs are shipped and retained far more widely than the
# request that produced them.
_REDACTED_QUERY_KEYS = frozenset({"key", "api_key", "apikey", "token", "secret", "password"})

# Endpoints polled by container health checks and load balancers. Logged at
# DEBUG so a probe every few seconds does not bury the traffic worth reading.
_PROBE_PATHS = frozenset({"/health/live", "/health/ready"})


def _safe_query(raw: str) -> str:
    """The query string with any credential-shaped value replaced."""
    from urllib.parse import parse_qsl, urlencode

    pairs = parse_qsl(raw, keep_blank_values=True)
    return urlencode(
        [(k, "[redacted]" if k.lower() in _REDACTED_QUERY_KEYS else v) for k, v in pairs]
    )


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Bind a request id for each request, echo it back, and log the result.

    The access line lives here rather than in uvicorn because uvicorn's is
    written by the server, outside the contextvar's scope, so it cannot carry the
    correlation id - which is the one field that ties it to the application lines
    the same request produced. It also has no duration.

    A request that raises is logged too, with the status the client will actually
    receive, and the exception re-raised for the handlers above. A failure that
    left no trace in the access log was the legacy behaviour and made "the screen
    was blank at 14:03" unanswerable.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Logged while the contextvar is still bound, so the access line
            # carries the same id as the traceback logged above it.
            self._log(request, 500, started)
            raise
        else:
            self._log(request, response.status_code, started)
        finally:
            request_id_var.reset(token)

        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @staticmethod
    def _log(request: Request, status: int, started: float) -> None:
        path = request.url.path
        query = _safe_query(request.url.query)
        client = request.client.host if request.client else "-"
        level = logging.DEBUG if path in _PROBE_PATHS else logging.INFO

        access_logger.log(
            level,
            '%s "%s %s%s" %s %.1fms',
            client,
            request.method,
            path,
            f"?{query}" if query else "",
            status,
            (time.perf_counter() - started) * 1000,
        )


def bind_task_id(task_id: str | None) -> None:
    """Bind a Celery task id as the correlation id inside a worker."""
    request_id_var.set(task_id)
