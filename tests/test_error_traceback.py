"""A 500 leaves its traceback in the log."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from archihub.core.errors import register_exception_handlers


def test_an_unexpected_error_is_logged_with_its_traceback(caplog):
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    def boom():
        raise RuntimeError("the real cause")

    with caplog.at_level(logging.ERROR, logger="archihub.core.errors"):
        response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    assert "the real cause" not in response.text
    record = next(r for r in caplog.records if "Unhandled exception" in r.getMessage())
    assert record.exc_info and record.exc_info[1].args == ("the real cause",)
