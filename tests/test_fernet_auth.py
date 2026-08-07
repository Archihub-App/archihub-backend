"""The three Fernet API-key authenticators.

The variants look like copy-paste in the original but check different fields on
the user document. Confusing them would let an ordinary public API key act as an
administrative one, so the differences are pinned here explicitly:

    fernet_authenticate         admin -> adminToken, else token
    public_fernet_authenticate  admin -> token,      else token
    node_fernet_authenticate    admin only -> nodeToken

The error-reporting asymmetry is pinned too: everything collapses to a generic
"Invalid or expired token" except the weekly rate limit, whose message is meant
to reach the caller.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from archihub.core.errors import AuthenticationError, RateLimitError
from archihub.core.security import fernet as fernet_auth
from archihub.core.security import tokens

FERNET_KEY = Fernet.generate_key().decode()
JWT_SECRET = "shared-secret-between-both-stacks-0123456789"
USERNAME = "api-user@example.test"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from archihub.core.settings import Settings, get_settings

    get_settings.cache_clear()
    settings = Settings(SECRET_KEY="s", JWT_SECRET_KEY=JWT_SECRET, FERNET_KEY=FERNET_KEY)
    monkeypatch.setattr(fernet_auth, "get_settings", lambda: settings)
    monkeypatch.setattr(tokens, "get_settings", lambda: settings)
    yield
    get_settings.cache_clear()


def make_key(username: str = USERNAME) -> str:
    """Build an API key the way the backend does: a JWT, then Fernet-encrypted."""
    inner = tokens.create_access_token(username)
    return Fernet(FERNET_KEY.encode()).encrypt(inner.encode()).decode()


@pytest.fixture
def user_store(monkeypatch):
    """Stub the users domain; returns a mutable user document."""
    state = {"user": {}, "is_admin": False, "requests_counted": 0, "rate_limited": False}

    def _get_by_username(username):
        return dict(state["user"])

    def _has_role(username, role):
        return role == "admin" and state["is_admin"]

    def _add_request(username):
        if state["rate_limited"]:
            raise RateLimitError("You have reached the limit of requests for this week")
        state["requests_counted"] += 1

    monkeypatch.setattr("archihub.api.users.services.get_by_username", _get_by_username)
    monkeypatch.setattr("archihub.api.users.services.has_role", _has_role)
    monkeypatch.setattr("archihub.api.users.services.add_request", _add_request)
    return state


# ---------------------------------------------------------------------------
# The field asymmetry between the three variants
# ---------------------------------------------------------------------------


def test_regular_user_authenticates_against_token_field(user_store):
    key = make_key()
    user_store["user"] = {"token": key, "adminToken": "something-else"}

    identity = fernet_auth.fernet_authenticate(f"Bearer {key}")
    assert identity.username == USERNAME
    assert identity.is_admin is False


def test_admin_authenticates_against_adminToken_field(user_store):
    """An admin key must be the admin key - not merely a valid key."""
    key = make_key()
    user_store["is_admin"] = True
    user_store["user"] = {"token": key, "adminToken": "the-real-admin-key"}

    # Presenting the ordinary token as an admin must fail...
    with pytest.raises(AuthenticationError):
        fernet_auth.fernet_authenticate(f"Bearer {key}")

    # ...while the adminToken succeeds.
    user_store["user"] = {"token": "other", "adminToken": key}
    identity = fernet_auth.fernet_authenticate(f"Bearer {key}")
    assert identity.is_admin is True


def test_public_api_checks_token_even_for_admins(user_store):
    """publicFernetAuthenticate checks `token` in BOTH branches.

    Not a bug carried over: on the public API an admin authenticates with their
    ordinary key. If this were "fixed" to check adminToken, every admin's public
    API integration would break.
    """
    key = make_key()
    user_store["is_admin"] = True
    user_store["user"] = {"token": key, "adminToken": "unused-here"}

    identity = fernet_auth.public_fernet_authenticate(f"Bearer {key}")
    assert identity.username == USERNAME
    assert identity.is_admin is True


def test_node_auth_requires_admin(user_store):
    key = make_key()
    user_store["is_admin"] = False
    user_store["user"] = {"nodeToken": key}

    with pytest.raises(AuthenticationError) as exc:
        fernet_auth.node_fernet_authenticate(f"Bearer {key}")
    assert exc.value.message == fernet_auth.MSG_NO_PERMISSION


def test_node_auth_checks_nodeToken(user_store):
    key = make_key()
    user_store["is_admin"] = True
    user_store["user"] = {"token": key, "adminToken": key, "nodeToken": "the-node-key"}

    with pytest.raises(AuthenticationError):
        fernet_auth.node_fernet_authenticate(f"Bearer {key}")

    user_store["user"] = {"nodeToken": key}
    assert fernet_auth.node_fernet_authenticate(f"Bearer {key}").username == USERNAME


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


def test_correctly_signed_but_superseded_key_is_refused(user_store):
    """Revocation works by overwriting the stored key.

    A key remains a valid, correctly-signed JWT after being rotated, so
    validation cannot stop at the signature - it must compare against the stored
    string.
    """
    old_key = make_key()
    user_store["user"] = {"token": make_key()}  # a newer key replaced it

    with pytest.raises(AuthenticationError) as exc:
        fernet_auth.fernet_authenticate(f"Bearer {old_key}")
    assert exc.value.message == fernet_auth.MSG_INVALID


def test_user_without_the_field_is_refused(user_store):
    """An absent key field must not compare equal to anything."""
    key = make_key()
    user_store["user"] = {}

    with pytest.raises(AuthenticationError):
        fernet_auth.fernet_authenticate(f"Bearer {key}")


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------


def test_non_admin_requests_are_metered(user_store):
    key = make_key()
    user_store["user"] = {"token": key}

    fernet_auth.fernet_authenticate(f"Bearer {key}")
    assert user_store["requests_counted"] == 1


def test_admin_requests_are_not_metered(user_store):
    key = make_key()
    user_store["is_admin"] = True
    user_store["user"] = {"adminToken": key}

    fernet_auth.fernet_authenticate(f"Bearer {key}")
    assert user_store["requests_counted"] == 0


def test_node_requests_are_not_metered(user_store):
    """Inter-node traffic must not consume a user's public-API quota."""
    key = make_key()
    user_store["is_admin"] = True
    user_store["user"] = {"nodeToken": key}

    fernet_auth.node_fernet_authenticate(f"Bearer {key}")
    assert user_store["requests_counted"] == 0


def test_rate_limit_message_reaches_the_caller(user_store):
    """The one failure that is NOT hidden behind the generic message.

    A throttled integrator needs to know they are throttled rather than broken.
    """
    key = make_key()
    user_store["user"] = {"token": key}
    user_store["rate_limited"] = True

    with pytest.raises(RateLimitError) as exc:
        fernet_auth.fernet_authenticate(f"Bearer {key}")
    assert exc.value.status_code == 429
    assert "limit of requests" in exc.value.message


# ---------------------------------------------------------------------------
# Failures that must stay opaque
# ---------------------------------------------------------------------------


def test_missing_header_is_reported_distinctly(user_store):
    with pytest.raises(AuthenticationError) as exc:
        fernet_auth.fernet_authenticate(None)
    assert exc.value.message == fernet_auth.MSG_NO_TOKEN


def test_garbage_key_gives_the_generic_message(user_store):
    with pytest.raises(AuthenticationError) as exc:
        fernet_auth.fernet_authenticate("Bearer not-a-fernet-token")
    assert exc.value.message == fernet_auth.MSG_GENERIC


def test_key_encrypted_with_a_different_fernet_key_is_refused(user_store):
    foreign = Fernet(Fernet.generate_key())
    forged = foreign.encrypt(tokens.create_access_token(USERNAME).encode()).decode()

    with pytest.raises(AuthenticationError) as exc:
        fernet_auth.fernet_authenticate(f"Bearer {forged}")
    assert exc.value.message == fernet_auth.MSG_GENERIC


def test_unknown_user_is_reported_as_such(user_store, monkeypatch):
    from archihub.core.errors import NotFoundError

    key = make_key()

    def _missing(username):
        raise NotFoundError("User not found", status_code=404)

    monkeypatch.setattr("archihub.api.users.services.get_by_username", _missing)

    with pytest.raises(AuthenticationError) as exc:
        fernet_auth.fernet_authenticate(f"Bearer {key}")
    assert exc.value.message == fernet_auth.MSG_NO_USER


def test_expired_inner_token_is_reported_as_expired(user_store):
    from datetime import timedelta

    inner = tokens.create_access_token(USERNAME, expires_delta=timedelta(seconds=-10))
    key = Fernet(FERNET_KEY.encode()).encrypt(inner.encode()).decode()
    user_store["user"] = {"token": key}

    with pytest.raises(AuthenticationError) as exc:
        fernet_auth.fernet_authenticate(f"Bearer {key}")
    assert exc.value.message == fernet_auth.MSG_EXPIRED


def test_auth_messages_are_translated(user_store, monkeypatch):
    """Auth failures reach the caller in the instance's language.

    The whole suite pins English for determinism (see tests/conftest.py), so
    this asserts the Spanish path explicitly - it is the behaviour real
    deployments see, and it is what proves the flask-babel replacement is wired
    all the way through the auth layer, not just importable.
    """
    import time

    from archihub.core import i18n

    i18n._locale_cache = ("es", time.monotonic())

    with pytest.raises(AuthenticationError) as exc:
        fernet_auth.fernet_authenticate(None)

    assert exc.value.message != fernet_auth.MSG_NO_TOKEN  # i.e. not the English source
    assert exc.value.message == "No se ha enviado el token de autenticación"


def test_key_without_bearer_prefix_is_accepted(user_store):
    """Legacy split on whitespace and took [1], but tolerated a bare key.

    Preserved so existing integrations that omit the prefix keep working.
    """
    key = make_key()
    user_store["user"] = {"token": key}
    assert fernet_auth.fernet_authenticate(key).username == USERNAME
