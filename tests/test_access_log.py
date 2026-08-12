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
    from archihub.core.errors import register_exception_handlers

    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    # The real handlers, so an unhandled exception takes the same path it takes
    # in the application: `_handle_unexpected` logs it and answers 500.
    register_exception_handlers(app)

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


def _access_lines(caplog) -> list[str]:
    """Only the access records.

    Selected by logger name rather than taking the last record, because a failing
    request also logs a traceback - from a handler that runs *outside* this
    middleware, so it lands after the access line.
    """
    return [r.getMessage() for r in caplog.records if r.name == "archihub.access"]


def test_a_successful_request_is_logged_with_its_status(client, caplog):
    with caplog.at_level(logging.INFO, logger="archihub.access"):
        client.get("/ok")

    line = _access_lines(caplog)[-1]
    assert '"GET /ok"' in line
    assert " 200 " in line


def test_the_line_reports_the_real_status_not_just_success(client, caplog):
    with caplog.at_level(logging.INFO, logger="archihub.access"):
        client.get("/missing")

    assert " 404 " in _access_lines(caplog)[-1]


def test_a_request_that_raises_is_still_logged(client, caplog):
    """A failure that left no trace in the access log makes "the screen was
    blank at 14:03" unanswerable."""
    with caplog.at_level(logging.INFO, logger="archihub.access"):
        client.get("/boom")

    assert " 500 " in _access_lines(caplog)[-1]


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
    access = [r for r in caplog.records if r.name == "archihub.access"]
    assert access[-1].request_id == "correlate-me"


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

    assert _access_lines(caplog) == []

    with caplog.at_level(logging.DEBUG, logger="archihub.access"):
        client.get("/health/live")

    assert '"GET /health/live"' in _access_lines(caplog)[-1]


def test_the_query_string_is_logged(client, caplog):
    with caplog.at_level(logging.INFO, logger="archihub.access"):
        client.get("/ok?page=2&sortOrder=desc")

    assert "page=2" in _access_lines(caplog)[-1]


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


# ---------------------------------------------------------------------------
# Turning it off
#
# ENVIRONMENT_NAME does NOT control this. That variable names the instance - the
# Mongo database and the index prefix are built from it - so renaming a database
# must not change how the application logs. The mode is ENVIRONMENT/FLASK_ENV.
# ---------------------------------------------------------------------------


def _settings(**overrides):
    """Settings built from the arguments alone.

    `_env_file=None` matters: without it pydantic-settings reads the developer's
    own `.env`, whose `FLASK_ENV='DEV'` would make every case here look like DEV
    regardless of what the test passed. `flask_env` is also defaulted explicitly,
    since it is the legacy spelling that wins over `ENVIRONMENT`.
    """
    from archihub.core.settings import Settings

    base = {
        "secret_key": "x",
        "jwt_secret_key": "y",
        "fernet_key": "z",
        "FLASK_ENV": None,
    }
    return Settings(_env_file=None, **{**base, **overrides})


def test_the_access_log_follows_the_environment_by_default():
    assert _settings(ENVIRONMENT="DEV").access_log_enabled is True
    assert _settings(ENVIRONMENT="PROD").access_log_enabled is False


def test_the_legacy_flask_env_spelling_is_honoured():
    assert _settings(ENVIRONMENT="PROD", FLASK_ENV="DEV").access_log_enabled is True


def test_an_explicit_setting_overrides_the_environment():
    """An operator who wants the lines in PROD - where they are the only record
    of which requests reached the application - can have them."""
    assert _settings(ENVIRONMENT="PROD", ACCESS_LOG="true").access_log_enabled is True
    assert _settings(ENVIRONMENT="DEV", ACCESS_LOG="false").access_log_enabled is False


def test_the_instance_name_does_not_control_logging():
    """ENVIRONMENT_NAME builds the database name; it is not a mode switch."""
    assert _settings(ENVIRONMENT="PROD", ENVIRONMENT_NAME="DEV").access_log_enabled is False


@pytest.fixture
def reconfigure(caplog):
    """Apply a real `configure_logging` and keep the test's capture working.

    `configure_logging` clears root's handlers - correct at startup, where it
    installs the one true handler - which also removes pytest's capture handler.
    Without re-adding it every assertion about what was logged passes vacuously,
    including "nothing was logged".
    """
    from archihub.core.logging import configure_logging

    def apply(**kwargs):
        configure_logging(**kwargs)
        logging.getLogger().addHandler(caplog.handler)

    yield apply
    configure_logging(level="INFO", json_output=False, access_log=True)


def test_configure_logging_can_silence_the_access_line(client, caplog, reconfigure):
    reconfigure(level="INFO", json_output=False, access_log=False)

    with caplog.at_level(logging.DEBUG):
        client.get("/ok")

    assert _access_lines(caplog) == []


def test_the_capture_really_would_have_seen_it(client, caplog, reconfigure):
    """Guards the test above: it asserts an absence, so it has to be shown that
    the same setup does observe the line when it is switched on."""
    reconfigure(level="INFO", json_output=False, access_log=True)

    with caplog.at_level(logging.DEBUG):
        client.get("/ok")

    assert _access_lines(caplog) != []


def test_silencing_the_access_line_does_not_silence_errors(client, caplog, reconfigure):
    """Verbosity and fault reporting are different things: an operator who turns
    the access log off must still be told when a request blew up."""
    reconfigure(level="INFO", json_output=False, access_log=False)

    with caplog.at_level(logging.ERROR):
        client.get("/boom")

    assert any(r.levelno >= logging.ERROR for r in caplog.records)
