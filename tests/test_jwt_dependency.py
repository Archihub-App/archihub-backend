"""JWT and role dependencies, exercised through real routes.

Checks the wire contract, not just the helper functions: status codes and body
shape are what ``upgrade_front`` and ``ArchiHUBTestRunner`` actually observe.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from archihub.core.errors import register_exception_handlers
from archihub.core.security import tokens
from archihub.core.security.jwt import CurrentUser, get_current_user, require_role_any

JWT_SECRET = "shared-secret-between-both-stacks-0123456789"
USERNAME = "alice@example.test"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from archihub.core.settings import Settings, get_settings

    get_settings.cache_clear()
    settings = Settings(SECRET_KEY="s", JWT_SECRET_KEY=JWT_SECRET, FERNET_KEY="f")
    monkeypatch.setattr(tokens, "get_settings", lambda: settings)
    yield
    get_settings.cache_clear()


@pytest.fixture
def roles(monkeypatch):
    """Control what roles the authenticated user appears to hold."""
    granted: set[str] = set()
    monkeypatch.setattr(
        "archihub.api.users.services.has_role",
        lambda username, role: role in granted,
    )
    return granted


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/me")
    def me(current_user: CurrentUser = Depends(get_current_user)):
        return {"user": current_user.username}

    @app.get("/admin-only", dependencies=[Depends(require_role_any("admin"))])
    def admin_only():
        return {"ok": True}

    @app.get("/admin-or-processing", dependencies=[Depends(require_role_any("admin", "processing"))])
    def admin_or_processing():
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_valid_token_authenticates(client: TestClient):
    response = client.get("/me", headers=auth(tokens.create_access_token(USERNAME)))
    assert response.status_code == 200
    assert response.json() == {"user": USERNAME}


def test_missing_header_returns_401_with_legacy_message(client: TestClient):
    response = client.get("/me")
    assert response.status_code == 401
    assert response.json() == {"msg": tokens.MSG_MISSING_HEADER}


def test_expired_token_returns_401(client: TestClient):
    from datetime import timedelta

    token = tokens.create_access_token(USERNAME, expires_delta=timedelta(seconds=-10))
    response = client.get("/me", headers=auth(token))
    assert response.status_code == 401
    assert response.json() == {"msg": tokens.MSG_EXPIRED}


def test_malformed_token_returns_422_matching_legacy(client: TestClient):
    """flask_jwt_extended returns 422 (not 401) for an unusable token.

    Preserved deliberately: upgrade_front does exact status-code comparisons in
    ~187 places, so collapsing this into 401 would be a wire change smuggled in
    under a framework swap. See InvalidTokenError's docstring.
    """
    response = client.get("/me", headers=auth("not.a.jwt"))
    assert response.status_code == 422
    assert response.json() == {"msg": tokens.MSG_INVALID_TOKEN}


def test_error_body_uses_the_msg_envelope(client: TestClient):
    """Every error keeps the {"msg": ...} shape the frontend reads."""
    for headers in ({}, auth("garbage")):
        body = client.get("/me", headers=headers).json()
        assert set(body) == {"msg"}
        assert isinstance(body["msg"], str)


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


def test_role_holder_is_allowed(client: TestClient, roles):
    roles.add("admin")
    response = client.get("/admin-only", headers=auth(tokens.create_access_token(USERNAME)))
    assert response.status_code == 200


def test_missing_role_returns_403_not_401(client: TestClient, roles):
    """The systematic correction: permission failures are 403.

    Legacy used 401 at ~223 sites and 403 exactly once, conflating "I don't know
    who you are" with "I know, and no". A 401 also tells the frontend to bounce
    the user to a login screen, which is the wrong remedy when they are already
    signed in and simply lack a role.
    """
    response = client.get("/admin-only", headers=auth(tokens.create_access_token(USERNAME)))
    assert response.status_code == 403


def test_authorisation_still_requires_authentication(client: TestClient, roles):
    """A role-guarded route must 401 (not 403) when no credentials are sent."""
    roles.add("admin")
    assert client.get("/admin-only").status_code == 401


def test_any_of_several_roles_suffices(client: TestClient, roles):
    roles.add("processing")
    response = client.get(
        "/admin-or-processing", headers=auth(tokens.create_access_token(USERNAME))
    )
    assert response.status_code == 200


def test_unrelated_role_does_not_grant_access(client: TestClient, roles):
    roles.add("editor")
    response = client.get(
        "/admin-or-processing", headers=auth(tokens.create_access_token(USERNAME))
    )
    assert response.status_code == 403


def test_security_is_documented_automatically(client: TestClient):
    """A route cannot enforce auth while forgetting to document it.

    In the legacy app the Flasgger `security:` block was hand-written per route
    and separate from the `@jwt_required()` that actually enforced it, so the two
    could drift. Here both come from the same dependency, so they cannot.
    """
    spec = client.app.openapi()

    assert "JWT" in spec["components"]["securitySchemes"]
    assert spec["components"]["securitySchemes"]["JWT"]["scheme"] == "bearer"

    for path in ("/me", "/admin-only", "/admin-or-processing"):
        assert spec["paths"][path]["get"]["security"] == [{"JWT": []}], path


def test_refresh_token_cannot_reach_a_protected_route(client: TestClient, roles):
    """End-to-end guard against the type-confusion bug PyJWT does not catch."""
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    refresh = pyjwt.encode(
        {"sub": USERNAME, "type": "refresh", "iat": now, "nbf": now,
         "exp": now + timedelta(days=30)},
        JWT_SECRET,
        algorithm="HS256",
    )
    roles.add("admin")
    assert client.get("/admin-only", headers=auth(refresh)).status_code == 422
