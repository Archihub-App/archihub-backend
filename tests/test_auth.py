"""Login, throttling and directory authentication.

The properties asserted here are the reason this module exists in the shape it
does; several would be easy to lose to a refactor that looks like a cleanup.
"""

from __future__ import annotations

import json
import threading
import time

import bcrypt
import pytest

from archihub.api.auth import ldap_auth, rate_limit, services


@pytest.fixture
def store(monkeypatch):
    """In-memory stand-in for the Redis attempt store.

    Redis runs a script as one atomic operation; the stand-in runs the same
    semantics under a lock, so concurrency tests exercise the real contract.
    """

    class FakeRedis:
        def __init__(self):
            self.data: dict = {}
            self.ttls: dict = {}
            self.available = True
            self.lock = threading.Lock()

        def _check(self):
            if not self.available:
                raise ConnectionError("redis is down")

        def _recent(self, key, cutoff):
            raw = self.data.get(key)
            stored = json.loads(raw) if raw else []
            return [ts for ts in stored if isinstance(ts, (int, float)) and ts > cutoff]

        def eval(self, script, numkeys, *args):
            self._check()
            keys, argv = list(args[:numkeys]), [float(a) for a in args[numkeys:]]
            with self.lock:
                if script is rate_limit._ACQUIRE:
                    now, window = argv[0], argv[1]
                    lists = []
                    for index, key in enumerate(keys, start=1):
                        recent = self._recent(key, now - window)
                        if len(recent) >= argv[1 + index]:
                            return index
                        lists.append(recent)
                    for key, recent in zip(keys, lists):
                        self.data[key] = json.dumps(recent + [now])
                        self.ttls[key] = window
                    return 0
            raise AssertionError("unexpected script")

        def delete(self, key):
            self._check()
            self.data.pop(key, None)

        def attempts(self, scope, value):
            return len(json.loads(self.data.get(rate_limit._key(scope, value)) or "[]"))

    fake = FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis", lambda: fake)
    return fake


@pytest.fixture
def users(monkeypatch):
    """Control what the users domain reports, without a database."""
    state: dict = {"user": None, "created": []}

    monkeypatch.setattr("archihub.api.users.services.get_user", lambda u: state["user"])
    monkeypatch.setattr(
        "archihub.api.users.services.register_user",
        lambda body: (state["created"].append(body), ({"msg": "ok"}, 201))[1],
    )
    monkeypatch.setattr(services, "_register_log", lambda *a, **k: None)
    return state


def make_user(username="alice@example.test", password="correct-horse"):
    return {
        "username": username,
        "password": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
    }


# ---------------------------------------------------------------------------
# Enumeration resistance
# ---------------------------------------------------------------------------


def test_unknown_account_and_wrong_password_are_indistinguishable(store, users):
    """Byte-identical responses.

    Any observable difference lets a caller discover which usernames exist.
    ArchiHUBTestRunner asserts this too.
    """
    users["user"] = None
    unknown = services.archihub_login("nobody@example.invalid", "whatever")

    users["user"] = make_user()
    wrong = services.archihub_login("alice@example.test", "wrong-password")

    assert unknown == wrong
    assert unknown[1] == 401


def test_password_is_verified_even_for_unknown_accounts(store, users, monkeypatch):
    """bcrypt runs either way, so response time does not leak existence.

    bcrypt is deliberately slow; skipping it when the account is absent makes
    those requests measurably faster and rebuilds the oracle the identical
    responses were meant to remove.
    """
    calls = []
    real_checkpw = bcrypt.checkpw
    monkeypatch.setattr(
        services.bcrypt, "checkpw", lambda p, h: (calls.append(1), real_checkpw(p, h))[1]
    )

    users["user"] = None
    services.archihub_login("nobody@example.invalid", "whatever")

    assert calls, "no password comparison was performed for an unknown account"


def test_malformed_stored_hash_reads_as_wrong_password(store, users):
    """A corrupt hash must not distinguish that account from any other."""
    users["user"] = {"username": "alice", "password": "not-a-bcrypt-hash"}
    payload, status = services.archihub_login("alice", "anything")
    assert status == 401


def test_successful_login_returns_a_usable_token(store, users):
    users["user"] = make_user()
    payload, status = services.archihub_login("alice@example.test", "correct-horse")

    assert status == 200
    from archihub.core.security import tokens

    claims = tokens.decode_access_token(payload["access_token"])
    assert claims["sub"] == "alice@example.test"


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------


def test_repeated_failures_are_throttled(store, users):
    users["user"] = None
    for _ in range(rate_limit.MAX_ATTEMPTS_PER_USERNAME):
        services.archihub_login("target@example.test", "wrong")

    payload, status = services.archihub_login("target@example.test", "wrong")
    assert status == 429


def test_the_correct_password_is_refused_once_the_account_is_throttled(store, users):
    """Past the limit the credentials are not even checked."""
    users["user"] = make_user()
    for _ in range(rate_limit.MAX_ATTEMPTS_PER_USERNAME):
        services.archihub_login("alice@example.test", "wrong")

    payload, status = services.archihub_login("alice@example.test", "correct-horse")
    assert status == 429


def test_simultaneous_guesses_cannot_exceed_the_limit(store, users):
    """A burst of parallel attempts gets exactly the budget."""
    users["user"] = None
    statuses = []

    def guess():
        statuses.append(services.archihub_login("target@example.test", "wrong")[1])

    threads = [threading.Thread(target=guess) for _ in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses.count(401) == rate_limit.MAX_ATTEMPTS_PER_USERNAME
    assert statuses.count(429) == 40 - rate_limit.MAX_ATTEMPTS_PER_USERNAME


def test_the_counter_ttl_matches_the_window_it_represents(store):
    """One constant drives both.

    If the stored record expires sooner than the window the code believes it is
    enforcing, the effective lockout silently becomes the shorter of the two.
    """
    rate_limit.acquire("someone", "10.0.0.1")

    assert set(store.ttls.values()) == {rate_limit.WINDOW_SECONDS}


def test_attempts_outside_the_window_do_not_count(store):
    stale = time.time() - rate_limit.WINDOW_SECONDS - 60
    store.data[rate_limit._key("user", "alice")] = json.dumps([stale] * 50)

    assert rate_limit.acquire("alice") is True


def test_throttling_applies_per_address_as_well_as_per_account(store):
    """Spraying across many accounts from one source must still be limited."""
    for index in range(rate_limit.MAX_ATTEMPTS_PER_IP):
        assert rate_limit.acquire(f"user{index}@example.test", "10.0.0.9") is True

    # A fresh account, but the same source address.
    assert rate_limit.acquire("brand-new@example.test", "10.0.0.9") is False


def test_a_refused_attempt_is_not_charged_to_the_other_budget(store):
    for _ in range(rate_limit.MAX_ATTEMPTS_PER_USERNAME):
        rate_limit.acquire("victim@example.test", "10.0.0.1")

    assert rate_limit.acquire("victim@example.test", "10.0.0.2") is False
    assert store.attempts("ip", "10.0.0.2") == 0


def test_one_accounts_lockout_does_not_affect_another(store):
    for _ in range(rate_limit.MAX_ATTEMPTS_PER_USERNAME):
        rate_limit.acquire("victim@example.test", "10.0.0.1")

    assert rate_limit.acquire("victim@example.test") is False
    assert rate_limit.acquire("someone-else@example.test") is True


def test_successful_login_clears_the_counter(store, users):
    users["user"] = make_user()
    for _ in range(3):
        services.archihub_login("alice@example.test", "wrong", client_ip="10.0.0.1")

    payload, status = services.archihub_login(
        "alice@example.test", "correct-horse", client_ip="10.0.0.1"
    )

    assert status == 200
    assert store.attempts("user", "alice@example.test") == 0
    assert store.attempts("ip", "10.0.0.1") == 0


def test_login_fails_closed_when_the_attempt_store_is_down(store, users):
    """The one place an outage should cost availability rather than protection.

    If this failed open, brute-force defence would disappear silently at exactly
    the moment the system is least healthy.
    """
    users["user"] = make_user()
    store.available = False

    payload, status = services.archihub_login("alice@example.test", "correct-horse")

    assert status == 429


# ---------------------------------------------------------------------------
# Directory input handling
# ---------------------------------------------------------------------------


# Scoped to the directory tests only. python-ldap is a compiled extension that
# needs system libraries, so it may be absent in a bare environment - but the
# login and throttling tests above must still run there.
try:
    import ldap  # noqa: F401

    HAS_LDAP = True
except ImportError:  # pragma: no cover
    HAS_LDAP = False

requires_ldap = pytest.mark.skipif(HAS_LDAP is False, reason="python-ldap not installed")


@requires_ldap
@pytest.mark.parametrize(
    "username",
    ["*", "alice)(uid=*", "a*b", "alice\\", "alice\x00admin", "(|(uid=*))"],
)
def test_filter_metacharacters_are_escaped(username):
    """A username reaching a search filter must not be able to alter it.

    These constructs are meaningful LDAP filter syntax; interpolating them raw
    changes which entries the query matches.
    """
    built = ldap_auth.build_user_filter(username)

    from ldap.filter import escape_filter_chars

    assert built == f"(uid={escape_filter_chars(username)})"
    # The raw form must not survive into the filter.
    assert built != f"(uid={username})"


@requires_ldap
@pytest.mark.parametrize("username", ["alice,dc=evil", "alice+uid=admin", 'alice"x', "alice<a>"])
def test_dn_metacharacters_are_escaped(username):
    """A username reaching a DN must not be able to re-point the bind."""
    built = ldap_auth.build_user_dn(username)

    from ldap.dn import escape_dn_chars

    assert built.startswith(f"uid={escape_dn_chars(username)},")
    assert built != f"uid={username},{ldap_auth.LDAP_USER_DN},{ldap_auth.LDAP_BASE_DN}"


@requires_ldap
def test_ordinary_usernames_are_unchanged():
    assert ldap_auth.build_user_filter("alice") == "(uid=alice)"


@requires_ldap
def test_empty_password_is_refused_before_contacting_the_directory(monkeypatch):
    """Many directories treat a zero-length password as an anonymous bind and
    answer success, which would read as a valid login."""
    monkeypatch.setattr(ldap_auth, "LDAP_HOST", "ldaps://directory.example")
    called = []
    monkeypatch.setattr(ldap_auth, "_import_ldap", lambda: called.append(1))

    assert ldap_auth.authenticate("alice", "") is None
    assert called == []


def test_directory_is_skipped_when_not_configured(monkeypatch):
    monkeypatch.setattr(ldap_auth, "LDAP_HOST", None)
    assert ldap_auth.is_enabled() is False
    assert ldap_auth.authenticate("alice", "password") is None
