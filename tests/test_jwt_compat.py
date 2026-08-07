"""Cross-stack JWT compatibility.

THE PHASE 1 GATE. Both stacks sign with the same ``JWT_SECRET_KEY``, and during
a phased cutover both may serve traffic at once, so tokens must be
interchangeable in BOTH directions:

* legacy-minted token accepted here - otherwise cutover signs every user out;
* new-minted token accepted by legacy - otherwise a user who logs in through
  the new stack cannot use any not-yet-ported route.

These tests exercise the real ``flask_jwt_extended`` library rather than a
hand-rolled approximation of its claim set, because the whole risk is that the
approximation is subtly wrong.

They also pin the rejection cases. PyJWT verifies signature, ``exp`` and
``nbf``, but has no concept of flask_jwt_extended's token *types* - an access
token and a refresh token differ by one claim and nothing else, so a naive
decode accepts a long-lived refresh token as proof of identity.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

flask = pytest.importorskip("flask", reason="legacy Flask stack not installed")
flask_jwt_extended = pytest.importorskip(
    "flask_jwt_extended", reason="legacy Flask stack not installed"
)

from flask import Flask, jsonify  # noqa: E402
from flask_jwt_extended import (  # noqa: E402
    JWTManager,
    create_access_token,
    create_refresh_token,
    get_jwt_identity,
    jwt_required,
)

from archihub.core.errors import AuthenticationError, InvalidTokenError  # noqa: E402
from archihub.core.security import tokens  # noqa: E402

SECRET = "shared-secret-between-both-stacks-0123456789"
USERNAME = "alice@example.test"


@pytest.fixture(autouse=True)
def _shared_secret(monkeypatch):
    """Point the new stack at the same secret the legacy app uses."""
    from archihub.core.settings import Settings, get_settings

    get_settings.cache_clear()
    settings = Settings(SECRET_KEY="s", JWT_SECRET_KEY=SECRET, FERNET_KEY="f")
    monkeypatch.setattr(tokens, "get_settings", lambda: settings)
    yield
    get_settings.cache_clear()


@pytest.fixture
def legacy_app() -> Flask:
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = SECRET
    JWTManager(app)

    @app.get("/protected")
    @jwt_required()
    def protected():
        return jsonify(user=get_jwt_identity())

    return app


# ---------------------------------------------------------------------------
# Direction 1: legacy mints -> new stack accepts
# ---------------------------------------------------------------------------


def test_legacy_token_is_accepted_by_new_stack(legacy_app: Flask):
    """Existing sessions must survive the cutover."""
    with legacy_app.app_context():
        token = create_access_token(identity=USERNAME, expires_delta=timedelta(days=1))

    claims = tokens.decode_access_token(token)
    assert tokens.get_identity(claims) == USERNAME


def test_legacy_long_lived_api_key_is_accepted(legacy_app: Flask):
    """The 365-day public API keys must keep working."""
    with legacy_app.app_context():
        token = create_access_token(identity=USERNAME, expires_delta=timedelta(days=365))

    assert tokens.get_identity(tokens.decode_access_token(token)) == USERNAME


def test_legacy_token_without_expiry_is_accepted(legacy_app: Flask):
    """API keys minted before expiries were introduced have no `exp` claim.

    Those were created with ``expires_delta=False`` and never expire. They must
    keep working until rotated, so `exp` is verified when present but not
    required.
    """
    with legacy_app.app_context():
        token = create_access_token(identity=USERNAME, expires_delta=False)

    assert tokens.get_identity(tokens.decode_access_token(token)) == USERNAME


# ---------------------------------------------------------------------------
# Direction 2: new stack mints -> legacy accepts
# ---------------------------------------------------------------------------


def test_new_token_is_accepted_by_legacy_flask(legacy_app: Flask):
    """A user logging in through the new stack must be able to use un-ported routes.

    This is the direction that is easy to forget, and it is what makes a
    gradual, route-by-route cutover possible at all.
    """
    token = tokens.create_access_token(USERNAME)

    response = legacy_app.test_client().get(
        "/protected", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.get_json() == {"user": USERNAME}


def test_new_token_claim_set_matches_legacy(legacy_app: Flask):
    """Same claims, so nothing downstream can tell the two mints apart.

    `csrf` is the deliberate exception: flask_jwt_extended only validates it for
    cookie-based tokens, and ArchiHUB sends tokens in the Authorization header.
    """
    with legacy_app.app_context():
        legacy_token = create_access_token(identity=USERNAME, expires_delta=timedelta(days=1))

    legacy_claims = tokens.decode_access_token(legacy_token)
    new_claims = tokens.decode_access_token(tokens.create_access_token(USERNAME))

    assert set(legacy_claims) - set(new_claims) == {"csrf"}
    assert set(new_claims) - set(legacy_claims) == set()
    for claim in ("sub", "type", "fresh"):
        assert new_claims[claim] == legacy_claims[claim]


# ---------------------------------------------------------------------------
# Rejection cases - the ones PyJWT does not handle by itself
# ---------------------------------------------------------------------------


def test_refresh_token_is_rejected(legacy_app: Flask):
    """A refresh token must not authenticate a request.

    It differs from an access token by the single `type` claim, which PyJWT does
    not inspect - so a naive decode accepts it. Refresh tokens are long-lived by
    design, making this a real privilege escalation, and legacy rejects it (422,
    "Only non-refresh tokens are allowed").
    """
    with legacy_app.app_context():
        refresh = create_refresh_token(identity=USERNAME)

    with pytest.raises(InvalidTokenError) as exc:
        tokens.decode_access_token(refresh)
    assert exc.value.status_code == 422
    assert exc.value.message == tokens.MSG_NOT_REFRESH


def test_future_nbf_token_is_rejected():
    """A not-yet-valid token must be refused."""
    import jwt as pyjwt
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    token = pyjwt.encode(
        {
            "sub": USERNAME,
            "type": "access",
            "iat": now,
            "nbf": now + timedelta(hours=1),
            "exp": now + timedelta(hours=2),
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        tokens.decode_access_token(token)


def test_expired_token_is_rejected_with_401(legacy_app: Flask):
    """Expiry is 401, matching legacy - distinct from the 422 cases."""
    with legacy_app.app_context():
        token = create_access_token(identity=USERNAME, expires_delta=timedelta(seconds=-10))

    with pytest.raises(AuthenticationError) as exc:
        tokens.decode_access_token(token)
    assert exc.value.status_code == 401
    assert exc.value.message == tokens.MSG_EXPIRED


def test_token_signed_with_another_key_is_rejected():
    import jwt as pyjwt

    forged = pyjwt.encode({"sub": USERNAME, "type": "access"}, "a-different-secret", algorithm="HS256")
    with pytest.raises(InvalidTokenError) as exc:
        tokens.decode_access_token(forged)
    assert exc.value.message == tokens.MSG_BAD_SIGNATURE


def test_algorithm_confusion_none_is_rejected():
    """A token claiming alg=none must never be trusted."""
    import jwt as pyjwt

    unsigned = pyjwt.encode({"sub": USERNAME, "type": "access"}, key="", algorithm="none")
    with pytest.raises(InvalidTokenError):
        tokens.decode_access_token(unsigned)


def test_token_without_subject_is_rejected():
    import jwt as pyjwt

    token = pyjwt.encode({"type": "access"}, SECRET, algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        tokens.decode_access_token(token)


def test_malformed_token_error_does_not_leak_internals():
    """Legacy echoed the raw decoder exception, leaking a Python codec error.

    Status code and body shape are preserved; the internals are not.
    """
    with pytest.raises(InvalidTokenError) as exc:
        tokens.decode_access_token("not.a.jwt")
    assert exc.value.message == tokens.MSG_INVALID_TOKEN
    assert "codec" not in exc.value.message


# ---------------------------------------------------------------------------
# Header parsing - 401 cases, messages reproduced verbatim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, tokens.MSG_MISSING_HEADER),
        ("", tokens.MSG_MISSING_HEADER),
        ("abc.def.ghi", tokens.MSG_MISSING_BEARER),
        ("Basic dXNlcjpwYXNz", tokens.MSG_MISSING_BEARER),
        ("Bearer", tokens.MSG_MISSING_BEARER),
    ],
)
def test_authorization_header_errors_match_legacy(header, expected):
    with pytest.raises(AuthenticationError) as exc:
        tokens.extract_bearer_token(header)
    assert exc.value.status_code == 401
    assert exc.value.message == expected


def test_bearer_prefix_is_case_insensitive():
    token = tokens.create_access_token(USERNAME)
    assert tokens.extract_bearer_token(f"bearer {token}") == token
