"""API keys.

The property that matters most is negative: **nothing stored can be presented to
the API**. Several tests exist purely to keep that true.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from archihub.core.security import api_keys


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
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(api_keys, "_mongo", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# The core property
# ---------------------------------------------------------------------------


def test_the_secret_is_never_stored(mongo):
    """A database read must yield nothing that can authenticate.

    This is the whole point of the scheme. If it ever fails, a backup or a
    `mongodump` hands over working credentials for every user.
    """
    key = api_keys.create_key("alice")
    _key_id, secret = api_keys.parse_key(key)

    stored = mongo.rows[0]
    serialised = repr(stored)

    assert secret not in serialised
    assert key not in serialised
    assert stored["secret_hash"] != secret


def test_two_keys_are_never_the_same(mongo):
    keys = {api_keys.create_key("alice") for _ in range(50)}
    assert len(keys) == 50


def test_secret_carries_full_entropy(mongo):
    key = api_keys.create_key("alice")
    _key_id, secret = api_keys.parse_key(key)
    # 32 bytes url-safe base64 -> 43 characters.
    assert len(secret) >= 43


def test_listing_never_exposes_the_hash(mongo):
    api_keys.create_key("alice", name="ci")
    listed = api_keys.list_keys("alice")

    assert listed
    assert "secret_hash" not in listed[0]


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def test_a_valid_key_authenticates(mongo):
    key = api_keys.create_key("alice", api_keys.SCOPE_PUBLIC)
    identity = api_keys.verify_key(key)

    assert identity is not None
    assert identity.username == "alice"
    assert identity.scope == api_keys.SCOPE_PUBLIC


def test_a_tampered_secret_is_refused(mongo):
    key = api_keys.create_key("alice")
    tampered = key[:-1] + ("A" if key[-1] != "A" else "B")

    assert api_keys.verify_key(tampered) is None


def test_a_valid_secret_under_another_handle_is_refused(mongo):
    """The handle and the secret must belong together."""
    first = api_keys.create_key("alice")
    second = api_keys.create_key("bob")

    first_id, _ = api_keys.parse_key(first)
    _, second_secret = api_keys.parse_key(second)

    assert api_keys.verify_key(api_keys.format_key(first_id, second_secret)) is None


def test_unknown_handle_is_refused(mongo):
    assert api_keys.verify_key("ahk_ffffffffffffffff_whatever") is None


@pytest.mark.parametrize(
    "presented",
    ["", "not-a-key", "eyJhbGciOiJIUzI1NiJ9.payload.sig", "ahk_", "ahk_onlyonepart", "gAAAAABm..."],
)
def test_values_from_another_scheme_return_none(mongo, presented):
    """None rather than an exception - that is what lets the caller fall back to
    the legacy credential path for keys issued before this scheme."""
    assert api_keys.verify_key(presented) is None


def test_revoked_keys_stop_working(mongo):
    key = api_keys.create_key("alice")
    key_id, _ = api_keys.parse_key(key)

    assert api_keys.verify_key(key) is not None
    api_keys.revoke_key(key_id, "alice")
    assert api_keys.verify_key(key) is None


def test_a_user_cannot_revoke_someone_elses_key(mongo):
    key = api_keys.create_key("alice")
    key_id, _ = api_keys.parse_key(key)

    assert api_keys.revoke_key(key_id, "mallory") is False
    assert api_keys.verify_key(key) is not None


def test_expired_keys_stop_working(mongo):
    key = api_keys.create_key("alice", expires_in=timedelta(seconds=-1))
    assert api_keys.verify_key(key) is None


def test_a_key_without_an_expiry_keeps_working(mongo):
    key = api_keys.create_key("alice", expires_in=None)
    assert api_keys.verify_key(key) is not None


def test_scope_is_enforced(mongo):
    """A public key must not satisfy a node-only route."""
    key = api_keys.create_key("alice", api_keys.SCOPE_PUBLIC)

    assert api_keys.verify_key(key, required_scope=api_keys.SCOPE_PUBLIC) is not None
    assert api_keys.verify_key(key, required_scope=api_keys.SCOPE_NODE) is None


def test_revoke_all_disables_every_key(mongo):
    keys = [api_keys.create_key("alice") for _ in range(3)]
    api_keys.revoke_all("alice")

    assert all(api_keys.verify_key(k) is None for k in keys)


def test_unknown_scope_is_rejected_at_creation(mongo):
    with pytest.raises(ValueError):
        api_keys.create_key("alice", "root")


# ---------------------------------------------------------------------------
# Bookkeeping
# ---------------------------------------------------------------------------


def test_last_used_is_not_rewritten_on_every_request(mongo):
    """Writing per request would turn every read-only API call into a write.

    The value is only ever read by a human, so a coarse resolution is enough.
    """
    key = api_keys.create_key("alice")
    api_keys.verify_key(key)

    first = mongo.rows[0]["last_used_at"]
    assert isinstance(first, datetime)

    api_keys.verify_key(key)
    assert mongo.rows[0]["last_used_at"] == first


def test_last_used_is_refreshed_once_the_window_passes(mongo):
    key = api_keys.create_key("alice")
    api_keys.verify_key(key)

    mongo.rows[0]["last_used_at"] = datetime.now() - api_keys.LAST_USED_RESOLUTION * 2
    api_keys.verify_key(key)

    assert datetime.now() - mongo.rows[0]["last_used_at"] < timedelta(minutes=1)


def test_bookkeeping_failure_does_not_fail_the_request(mongo, monkeypatch):
    key = api_keys.create_key("alice")

    def _explode(*args, **kwargs):
        raise ConnectionError("write failed")

    monkeypatch.setattr(mongo, "update_record", _explode)
    assert api_keys.verify_key(key) is not None
