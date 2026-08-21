"""Authentication for the external APIs (`/adminApi`, `/publicApi`, node).

The three variants look like copy-paste and are not: they authorise different
things, and the differences are what these tests pin.

    authenticate_admin_api    any scope; the ROLE check is at the route
    authenticate_public_api   `public` scope only, metered
    authenticate_node_api     `node` scope only, administrators only, unmetered

Metering is the easiest of these to get wrong in a way nothing notices: a node
calling in to invalidate a cache must not spend a person's weekly public-API
quota, and no test that only asserts a status code would catch it.
"""

from __future__ import annotations

import pytest

from archihub.core.errors import AuthenticationError, RateLimitError
from archihub.core.security import api_auth, api_keys


class FakeMongo:
    def __init__(self):
        self.rows: list[dict] = []

    def insert_record(self, collection, record):
        self.rows.append(dict(record))

    def get_record(self, collection, filters, fields=None):
        for row in self.rows:
            if all(row.get(k) == v for k, v in filters.items()):
                return dict(row)
        return None

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        return [dict(r) for r in self.rows if all(r.get(k) == v for k, v in (filters or {}).items())]

    def update_record(self, collection, filters, update):
        for row in self.rows:
            if all(row.get(k) == v for k, v in filters.items()):
                row.update(update)
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()

    def update_records(self, collection, filters, update):
        for row in self.rows:
            if all(row.get(k) == v for k, v in filters.items()):
                row.update(update)


@pytest.fixture
def keys(monkeypatch):
    """A key store, with roles and metering stubbed so each is observable."""
    fake = FakeMongo()
    monkeypatch.setattr(api_keys, "_mongo", lambda: fake)

    admins = {"boss"}
    metered: list[str] = []

    monkeypatch.setattr(api_auth, "_is_admin", lambda username: username in admins)
    monkeypatch.setattr(api_auth, "_count_request", lambda username: metered.append(username))

    fake.admins = admins
    fake.metered = metered
    return fake


def issue(username: str, scope: str) -> str:
    return api_keys.create_key(username, scope)


# ---------------------------------------------------------------------------
# Scope is what separates the three APIs
# ---------------------------------------------------------------------------


def test_a_public_key_authenticates_against_the_public_api(keys):
    identity = api_auth.authenticate_public_api(f"Bearer {issue('alice', api_keys.SCOPE_PUBLIC)}")

    assert identity.username == "alice"
    assert identity.is_admin is False


def test_a_node_key_is_refused_by_the_public_api(keys):
    """A valid key of the wrong scope is still a refusal."""
    with pytest.raises(AuthenticationError):
        api_auth.authenticate_public_api(f"Bearer {issue('boss', api_keys.SCOPE_NODE)}")


def test_a_public_key_is_refused_by_the_node_api(keys):
    with pytest.raises(AuthenticationError):
        api_auth.authenticate_node_api(f"Bearer {issue('boss', api_keys.SCOPE_PUBLIC)}")


def test_the_node_api_requires_an_administrator(keys):
    """Scope alone is not enough - the account must be an admin too."""
    with pytest.raises(AuthenticationError):
        api_auth.authenticate_node_api(f"Bearer {issue('alice', api_keys.SCOPE_NODE)}")


def test_an_admin_node_key_authenticates(keys):
    identity = api_auth.authenticate_node_api(f"Bearer {issue('boss', api_keys.SCOPE_NODE)}")

    assert identity.username == "boss"
    assert identity.is_admin is True


def test_an_admin_key_authenticates_against_the_admin_api(keys):
    identity = api_auth.authenticate_admin_api(f"Bearer {issue('boss', api_keys.SCOPE_ADMIN)}")

    assert identity.is_admin is True


@pytest.mark.parametrize("scope", [api_keys.SCOPE_PUBLIC, api_keys.SCOPE_NODE, api_keys.SCOPE_VIZ])
def test_another_scope_is_refused_by_the_admin_api_even_for_an_admin(keys, scope):
    """A scope bounds what one credential can reach, so holding an
    administrator account must not turn every key that account owns into an
    administrative API key. The `node` key makes this concrete: it lives on
    every worker machine and goes over the network on each cache broadcast.
    """
    with pytest.raises(AuthenticationError):
        api_auth.authenticate_admin_api(f"Bearer {issue('boss', scope)}")


def test_the_admin_api_refuses_a_non_administrator_holding_an_admin_key(keys):
    """Two independent gates, not one written twice: the scope says which door
    the key opens, the role says who the holder is."""
    with pytest.raises(AuthenticationError):
        api_auth.authenticate_admin_api(f"Bearer {issue('alice', api_keys.SCOPE_ADMIN)}")


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------


def test_a_public_caller_is_metered(keys):
    api_auth.authenticate_public_api(f"Bearer {issue('alice', api_keys.SCOPE_PUBLIC)}")

    assert keys.metered == ["alice"]


def test_an_administrator_is_not_metered(keys):
    api_auth.authenticate_admin_api(f"Bearer {issue('boss', api_keys.SCOPE_ADMIN)}")

    assert keys.metered == []


def test_node_traffic_is_never_metered(keys):
    """Cache invalidation between nodes must not consume anyone's quota."""
    api_auth.authenticate_node_api(f"Bearer {issue('boss', api_keys.SCOPE_NODE)}")

    assert keys.metered == []


def test_the_rate_limit_message_reaches_the_caller(keys, monkeypatch):
    """Every other failure is hidden behind one generic message. This one is
    not: a throttled caller has to be able to tell "slow down" from "broken"."""
    def throttled(username):
        raise RateLimitError("You have reached the limit of requests for this week")

    monkeypatch.setattr(api_auth, "_count_request", throttled)

    with pytest.raises(RateLimitError):
        api_auth.authenticate_public_api(f"Bearer {issue('alice', api_keys.SCOPE_PUBLIC)}")


# ---------------------------------------------------------------------------
# Refusals say as little as possible
# ---------------------------------------------------------------------------


def test_a_missing_header_is_reported_distinctly(keys):
    """Not a leak: the caller sent nothing, so there is nothing to probe."""
    with pytest.raises(AuthenticationError) as excinfo:
        api_auth.authenticate_public_api(None)

    assert "provided" in str(excinfo.value).lower() or "proporcion" in str(excinfo.value).lower()


@pytest.mark.parametrize(
    "presented",
    [
        "Bearer not-a-key",
        "Bearer ahk_deadbeefdeadbeef_wrongsecret",
        "Bearer gAAAAABpAPMHsomethingthatlookslikeanoldkey",
        "Bearer ",
    ],
)
def test_every_bad_key_gives_the_SAME_message(keys, presented):
    """Unknown, malformed, wrong scheme - one message for all of them, so a
    probe cannot learn which of those it hit."""
    issue("alice", api_keys.SCOPE_PUBLIC)

    with pytest.raises(AuthenticationError) as excinfo:
        api_auth.authenticate_public_api(presented)

    assert str(excinfo.value) == api_auth._(api_auth.MSG_GENERIC)


def test_a_revoked_key_is_refused(keys):
    key = issue("alice", api_keys.SCOPE_PUBLIC)
    api_keys.revoke_all("alice")

    with pytest.raises(AuthenticationError):
        api_auth.authenticate_public_api(f"Bearer {key}")


def test_a_key_without_the_bearer_prefix_is_accepted(keys):
    """Callers of this API have always sent both forms."""
    identity = api_auth.authenticate_public_api(issue("alice", api_keys.SCOPE_PUBLIC))

    assert identity.username == "alice"


# ---------------------------------------------------------------------------
# The scheme that was removed
# ---------------------------------------------------------------------------


def test_a_fernet_ciphertext_no_longer_authenticates(keys):
    """The previous scheme stored a Fernet ciphertext on the user document and
    compared the presented string against it, so the stored value WAS the
    credential. Nothing issues or accepts those any more; a value shaped like
    one is just an unknown key.
    """
    with pytest.raises(AuthenticationError):
        api_auth.authenticate_node_api(
            "Bearer gAAAAABpAPMH" + "x" * 500
        )


def test_nothing_here_reads_a_credential_off_the_user_document():
    """The property the rewrite exists for: a database read must not yield
    anything presentable to the API. Asserted over the source, because the
    absence of a field lookup is not observable from behaviour.
    """
    import pathlib

    source = pathlib.Path(api_auth.__file__).read_text()
    for field in ("adminToken", "nodeToken", "vizToken"):
        assert field not in source, f"{field} is read again in api_auth.py"
