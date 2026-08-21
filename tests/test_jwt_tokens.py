"""Token acceptance and rejection.

TOKENS ISSUED BEFORE THIS BACKEND EXISTED ARE STILL IN CIRCULATION, and that is
what most of this file is about. An access token sits in every signed-in
browser's local storage, and API keys were issued with a 365-day expiry or with
none at all. All of them are signed with the same ``JWT_SECRET_KEY``, so they
must keep being accepted until they expire or are rotated - otherwise upgrading
signs every user out and breaks every integration on the same afternoon.

The claim sets below are written as LITERALS rather than produced by the library
that originally minted them. That is deliberate: the contract is the shape on
the wire, not any library's current idea of it, and a test that mints its own
input with the same code under test proves only that it agrees with itself.
The literals were taken from real tokens.

The rejection cases are the ones a plain signature check does not cover. PyJWT
verifies the signature, ``exp`` and ``nbf`` and stops there - it has no concept
of a token *type*, and an access token and a refresh token differ by one claim
and nothing else.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest

from archihub.core.errors import AuthenticationError, InvalidTokenError
from archihub.core.security import tokens

SECRET = "shared-secret-between-both-stacks-0123456789"
USERNAME = "alice@example.test"


@pytest.fixture(autouse=True)
def _shared_secret(monkeypatch):
    from archihub.core.settings import Settings, get_settings

    get_settings.cache_clear()
    settings = Settings(SECRET_KEY="s", JWT_SECRET_KEY=SECRET, FERNET_KEY="f")
    monkeypatch.setattr(tokens, "get_settings", lambda: settings)
    yield
    get_settings.cache_clear()


def _issued_token(
    *,
    token_type: str = "access",
    expires_in: timedelta | None = timedelta(days=1),
    subject: str | None = USERNAME,
    secret: str = SECRET,
) -> str:
    """A token in the shape previously issued to users.

    ``csrf`` is present because it was always minted; it is never validated for
    a token sent in the Authorization header, which is how this application
    sends them.
    """
    now = datetime.now(timezone.utc)
    claims = {
        "fresh": False,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "type": token_type,
        "nbf": now,
        "csrf": str(uuid.uuid4()),
    }
    if subject is not None:
        claims["sub"] = subject
    if expires_in is not None:
        claims["exp"] = now + expires_in
    return pyjwt.encode(claims, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Tokens already in circulation
# ---------------------------------------------------------------------------


def test_an_already_issued_token_is_accepted():
    """Existing sessions survive the upgrade."""
    claims = tokens.decode_access_token(_issued_token())
    assert tokens.get_identity(claims) == USERNAME


def test_a_long_lived_api_key_is_accepted():
    """The 365-day API keys must keep working until they are rotated."""
    token = _issued_token(expires_in=timedelta(days=365))
    assert tokens.get_identity(tokens.decode_access_token(token)) == USERNAME


def test_a_token_without_an_expiry_is_accepted():
    """Keys minted before expiries were introduced carry no `exp` claim.

    They never expire, so `exp` is verified when present and not required.
    """
    token = _issued_token(expires_in=None)
    assert tokens.get_identity(tokens.decode_access_token(token)) == USERNAME


def test_a_token_this_backend_mints_carries_the_same_claims():
    """What is issued now must be indistinguishable from what came before.

    `csrf` is the one exception - it is not minted any more, and nothing has
    ever validated it for a header-borne token.
    """
    previous = tokens.decode_access_token(_issued_token())
    current = tokens.decode_access_token(tokens.create_access_token(USERNAME))

    assert set(previous) - set(current) == {"csrf"}
    assert set(current) - set(previous) == set()
    for claim in ("sub", "type", "fresh"):
        assert current[claim] == previous[claim]


# ---------------------------------------------------------------------------
# Rejection cases
# ---------------------------------------------------------------------------


def test_a_refresh_token_cannot_authenticate():
    """A refresh token must not stand in for an access token.

    The two differ by the single `type` claim, which a signature check does not
    inspect. Refresh tokens are long-lived by design, so accepting one is a real
    privilege escalation rather than an untidiness.
    """
    with pytest.raises(InvalidTokenError) as exc:
        tokens.decode_access_token(_issued_token(token_type="refresh"))
    assert exc.value.status_code == 422
    assert exc.value.message == tokens.MSG_NOT_REFRESH


def test_a_not_yet_valid_token_is_rejected():
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


def test_an_expired_token_is_rejected_with_401():
    """Expiry is 401 - "I no longer know who you are" - not 422."""
    with pytest.raises(AuthenticationError) as exc:
        tokens.decode_access_token(_issued_token(expires_in=timedelta(seconds=-10)))
    assert exc.value.status_code == 401
    assert exc.value.message == tokens.MSG_EXPIRED


def test_a_token_signed_with_another_key_is_rejected():
    with pytest.raises(InvalidTokenError) as exc:
        tokens.decode_access_token(_issued_token(secret="a-different-secret"))
    assert exc.value.message == tokens.MSG_BAD_SIGNATURE


def test_a_token_claiming_alg_none_is_rejected():
    """An unsigned token must never be trusted, whatever it claims to be."""
    unsigned = pyjwt.encode({"sub": USERNAME, "type": "access"}, key="", algorithm="none")
    with pytest.raises(InvalidTokenError):
        tokens.decode_access_token(unsigned)


def test_a_token_without_a_subject_is_rejected():
    with pytest.raises(InvalidTokenError):
        tokens.decode_access_token(_issued_token(subject=None))


def test_a_malformed_token_error_does_not_leak_internals():
    """The message names the problem, never the decoder's own exception text."""
    with pytest.raises(InvalidTokenError) as exc:
        tokens.decode_access_token("not.a.jwt")
    assert exc.value.message == tokens.MSG_INVALID_TOKEN
    assert "codec" not in exc.value.message


# ---------------------------------------------------------------------------
# Header parsing
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
def test_authorization_header_errors(header, expected):
    with pytest.raises(AuthenticationError) as exc:
        tokens.extract_bearer_token(header)
    assert exc.value.status_code == 401
    assert exc.value.message == expected


def test_the_bearer_prefix_is_case_insensitive():
    token = tokens.create_access_token(USERNAME)
    assert tokens.extract_bearer_token(f"bearer {token}") == token
