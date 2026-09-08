"""chrQrUpload — the one-time credential a QR code carries.

This is the plugin's whole risk surface. The code is displayed on a screen, so
it can be photographed by anyone in the room; what keeps that from mattering is
that it expires quickly, that it is not a bearer token for anything else, and
that the first upload to present it spends it. Each of those is tested here,
including by presenting the same credential twice.
"""

from __future__ import annotations

import datetime

import pytest

plugin = pytest.importorskip(
    "archihub.plugins.chrQrUpload",
    reason="chrQrUpload is not installed in this checkout",
)


class FakeSettingsStore:
    """Stands in for the plugin's settings document."""

    def __init__(self):
        self.stored = {}

    def get_plugin_settings(self):
        return dict(self.stored)

    def set_plugin_settings(self, settings):
        self.stored = dict(settings)


@pytest.fixture
def credentials():
    return plugin.QRCredentials(FakeSettingsStore())


# ---------------------------------------------------------------------------
# Issue and redeem
# ---------------------------------------------------------------------------


def test_a_freshly_issued_credential_names_the_person_it_was_issued_to(credentials):
    presented = credentials.issue("alice@example.org").decode()
    assert credentials.redeem(presented) == "alice@example.org"


def test_a_credential_can_only_be_redeemed_once(credentials):
    """A photographed code is worthless once the intended device has used it."""
    presented = credentials.issue("alice@example.org").decode()
    credentials.redeem(presented)

    with pytest.raises(plugin.QRAuthenticationFailed):
        credentials.redeem(presented)


def test_asking_for_a_new_code_invalidates_the_one_already_on_screen(credentials):
    first = credentials.issue("alice@example.org").decode()
    credentials.issue("alice@example.org")

    with pytest.raises(plugin.QRAuthenticationFailed):
        credentials.redeem(first)


def test_two_people_hold_their_own_credentials_at_the_same_time(credentials):
    alice = credentials.issue("alice@example.org").decode()
    bob = credentials.issue("bob@example.org").decode()

    assert credentials.redeem(alice) == "alice@example.org"
    assert credentials.redeem(bob) == "bob@example.org"


@pytest.mark.parametrize("presented", ["", "garbage", "gAAAAABmnot-a-real-token", "a.b.c"])
def test_something_that_is_not_a_credential_is_refused(credentials, presented):
    with pytest.raises(plugin.QRAuthenticationFailed):
        credentials.redeem(presented)


def test_a_valid_token_that_was_never_issued_here_is_refused(credentials):
    """Signature and expiry pass; the mint was never recorded, so it is not one
    of this plugin's codes and does not authorise an upload."""
    from archihub.core.security import tokens

    token = tokens.create_access_token("alice@example.org")
    smuggled = plugin._fernet().encrypt(token.encode()).decode()

    with pytest.raises(plugin.QRAuthenticationFailed):
        credentials.redeem(smuggled)


def test_a_credential_whose_token_has_expired_is_refused(credentials):
    """The first of the two expiry checks: PyJWT's, on the token's own `exp`.

    Registered in the store by hand so the mint itself is not what fails - the
    point is that a recorded, genuinely-issued credential still stops working
    once its ten minutes are up.
    """
    from archihub.core.security import tokens

    token = tokens.create_access_token(
        "alice@example.org", expires_delta=datetime.timedelta(seconds=-1)
    )
    claims = _claims_without_checking_expiry(token)
    credentials._plugin.stored = {
        plugin.TOKEN_STORE: {
            claims["jti"]: {
                "user": "alice@example.org",
                "expires": datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(minutes=5),
            }
        }
    }
    presented = plugin._fernet().encrypt(token.encode()).decode()

    with pytest.raises(plugin.QRAuthenticationFailed):
        credentials.redeem(presented)


def test_a_credential_whose_recorded_mint_has_lapsed_is_refused(credentials):
    """The second check: our own bookkeeping, which is what stops the store
    from growing by one entry for every code that was displayed and ignored."""
    presented = credentials.issue("alice@example.org").decode()

    store = credentials._plugin.stored[plugin.TOKEN_STORE]
    for entry in store.values():
        entry["expires"] = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            minutes=1
        )

    with pytest.raises(plugin.QRAuthenticationFailed):
        credentials.redeem(presented)


def _claims_without_checking_expiry(token: str) -> dict:
    import jwt

    from archihub.core.settings import get_settings

    return jwt.decode(
        token,
        get_settings().jwt_secret_key,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )


# ---------------------------------------------------------------------------
# What is written down
# ---------------------------------------------------------------------------


def test_the_credential_itself_is_never_stored(credentials):
    """The settings document is rendered by the plugin settings screen. Holding
    the token there would put a live credential for every cataloguer who has
    opened their profile behind an admin page."""
    presented = credentials.issue("alice@example.org").decode()

    stored = credentials._plugin.stored[plugin.TOKEN_STORE]
    assert presented not in repr(stored)
    for jti, entry in stored.items():
        assert presented not in jti
        assert set(entry) == {"user", "expires"}


def test_what_is_stored_is_keyed_by_a_random_identifier_not_a_username(credentials):
    """Usernames are email addresses, and a dotted key is a field path."""
    credentials.issue("alice@example.org")

    keys = list(credentials._plugin.stored[plugin.TOKEN_STORE])
    assert keys and all("." not in key and "@" not in key for key in keys)


def test_expired_mints_are_dropped_rather_than_accumulating():
    now = datetime.datetime.now(datetime.timezone.utc)
    store = {
        "live": {"user": "a", "expires": now + datetime.timedelta(minutes=5)},
        "stale": {"user": "b", "expires": now - datetime.timedelta(minutes=5)},
        "malformed": "not an entry",
    }

    assert list(plugin._prune(store, now)) == ["live"]


def test_pruning_nothing_is_not_an_error():
    assert plugin._prune(None, datetime.datetime.now(datetime.timezone.utc)) == {}


# ---------------------------------------------------------------------------
# Reading the credential off the request
# ---------------------------------------------------------------------------


def test_a_bearer_header_yields_the_credential():
    assert plugin._bearer("Bearer abc123") == "abc123"
    assert plugin._bearer("bearer abc123") == "abc123"


@pytest.mark.parametrize("header", [None, "", "abc123", "Bearer", "Bearer   ", "Basic abc123"])
def test_anything_else_yields_no_credential(header):
    assert plugin._bearer(header) is None


# ---------------------------------------------------------------------------
# The QR image
# ---------------------------------------------------------------------------


def test_the_code_is_rendered_in_memory_as_a_png():
    """Nothing is written under a name built from the username, so there is no
    file to leave behind and no path assembled out of stored data."""
    pytest.importorskip("qrcode")

    png = plugin.render_qr_png(b"some-credential")

    assert png.startswith(b"\x89PNG\r\n\x1a\n")


# ---------------------------------------------------------------------------
# Merging the photographed pages
# ---------------------------------------------------------------------------


def test_an_upload_with_no_images_is_refused_rather_than_writing_an_empty_pdf():
    with pytest.raises(ValueError):
        plugin.merge_images_to_pdf([])


# ---------------------------------------------------------------------------
# The routes, through the real routing stack
# ---------------------------------------------------------------------------


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from archihub.core.errors import register_exception_handlers
    from archihub.plugins.framework.mounting import build_plugin

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(build_plugin(plugin.SLUG).build())
    return TestClient(app, raise_server_exceptions=False)


def test_a_qr_credential_is_not_handed_out_anonymously():
    """The response body of this route IS a credential."""
    assert _client().get("/chrQrUpload/qrcode").status_code == 401


def test_an_upload_with_no_credential_is_refused():
    """This route is deliberately not JWT-authenticated - the scanning device
    has no session - so the QR credential is the whole of its authorisation."""
    response = _client().post("/chrQrUpload/upload", data={"data": "{}"})
    assert response.status_code == 401


def test_an_upload_with_a_credential_that_is_not_ours_is_refused():
    response = _client().post(
        "/chrQrUpload/upload",
        data={"data": "{}"},
        headers={"Authorization": "Bearer not-a-credential"},
    )
    assert response.status_code == 401


def test_the_settings_routes_are_not_anonymous():
    client = _client()
    assert client.get("/chrQrUpload/settings/all").status_code == 401
    assert client.post("/chrQrUpload/settings", data={"data": "{}"}).status_code == 401
