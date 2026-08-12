"""The per-request access line.

Uvicorn's own access log is silenced (see `core.logging.configure_logging`)
because it is written by the server, outside the request-id contextvar's scope,
so it cannot carry the correlation id - the one field that ties a request to the
application lines it produced. `RequestIdMiddleware` emits the line instead.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from archihub.core.logging import (
    REQUEST_ID_HEADER,
    RequestIdFilter,
    RequestIdMiddleware,
    _safe_query,
    get_request_id,
)


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ok")
    def ok():
        return {"request_id": get_request_id()}

    @app.get("/missing")
    def missing():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="nope")

    @app.get("/boom")
    def boom():
        raise RuntimeError("unhandled")

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    return TestClient(app, raise_server_exceptions=False)


def test_a_successful_request_is_logged_with_its_status(client, caplog):
    with caplog.at_level(logging.INFO, logger="archihub.access"):
        client.get("/ok")

    line = caplog.records[-1].getMessage()
    assert '"GET /ok"' in line
    assert " 200 " in line


def test_the_line_reports_the_real_status_not_just_success(client, caplog):
    with caplog.at_level(logging.INFO, logger="archihub.access"):
        client.get("/missing")

    assert " 404 " in caplog.records[-1].getMessage()


def test_a_request_that_raises_is_still_logged(client, caplog):
    """A failure that left no trace in the access log makes "the screen was
    blank at 14:03" unanswerable."""
    with caplog.at_level(logging.INFO, logger="archihub.access"):
        client.get("/boom")

    assert " 500 " in caplog.records[-1].getMessage()


def test_the_access_line_carries_the_request_id(client, caplog):
    """The whole reason this is not uvicorn's access log.

    `RequestIdFilter` is what stamps the id onto a record, and it is installed on
    the application's handler by `configure_logging`. Adding it to caplog's
    handler tests the real composition - the filter reading the contextvar that
    the middleware bound - rather than the middleware alone.
    """
    caplog.handler.addFilter(RequestIdFilter())

    with caplog.at_level(logging.INFO, logger="archihub.access"):
        response = client.get("/ok", headers={REQUEST_ID_HEADER: "correlate-me"})

    assert response.headers[REQUEST_ID_HEADER] == "correlate-me"
    assert caplog.records[-1].request_id == "correlate-me"


def test_an_absent_inbound_id_is_generated_not_left_empty(client):
    response = client.get("/ok")

    generated = response.headers[REQUEST_ID_HEADER]
    assert generated and generated == response.json()["request_id"]


def test_the_id_is_reset_even_when_the_handler_raises(client):
    """Otherwise the next request served on this task inherits a stale id."""
    client.get("/boom")
    assert get_request_id() is None


def test_health_probes_do_not_log_at_info(client, caplog):
    """A probe every few seconds would bury the traffic worth reading."""
    with caplog.at_level(logging.INFO, logger="archihub.access"):
        client.get("/health/live")

    assert caplog.records == []

    with caplog.at_level(logging.DEBUG, logger="archihub.access"):
        client.get("/health/live")

    assert '"GET /health/live"' in caplog.records[-1].getMessage()


def test_the_query_string_is_logged(client, caplog):
    with caplog.at_level(logging.INFO, logger="archihub.access"):
        client.get("/ok?page=2&sortOrder=desc")

    assert "page=2" in caplog.records[-1].getMessage()


# ---------------------------------------------------------------------------
# Redaction
#
# Nothing in the API accepts a credential in the query string today. This keeps
# that true by default if one is ever added, because logs are shipped and
# retained far more widely than the request that produced them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["key", "api_key", "apikey", "token", "secret", "password"])
def test_a_credential_shaped_query_value_is_not_logged(key):
    assert _safe_query(f"{key}=hunter2") == f"{key}=%5Bredacted%5D"


def test_redaction_is_case_insensitive():
    assert "hunter2" not in _safe_query("API_KEY=hunter2")


def test_ordinary_values_survive_redaction():
    assert _safe_query("page=2&sortBy=createdAt") == "page=2&sortBy=createdAt"


def test_only_the_matching_pair_is_redacted():
    out = _safe_query("page=2&token=abc&sortOrder=desc")
    assert "page=2" in out and "sortOrder=desc" in out and "abc" not in out
