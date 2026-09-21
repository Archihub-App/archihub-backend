"""The memoisation layer, and the property that makes it safe to switch on.

Invalidation is not a call somebody remembers to make: a cached read declares
the collections it depends on, and writing to one of those collections through
the Mongo wrapper makes the entry unreachable. These tests pin that, because the
failure mode is an authorisation check answering from before a role was revoked
- a 200 that should have been a 403, with nothing logged either way.
"""

from __future__ import annotations

import pytest

from archihub.infra import cache as cache_module


class FakeRedis:
    """Enough Redis to exercise the decorator, with no socket.

    Deliberately not a mock: the key derivation and the generation arithmetic
    are the things under test, and a mock would assert the calls this
    implementation happens to make rather than the behaviour it owes.
    """

    def __init__(self):
        self.values: dict[str, str] = {}
        self.reads = 0

    def get(self, key):
        self.reads += 1
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = value

    def mget(self, keys):
        return [self.values.get(k) for k in keys]

    def register_script(self, _source):
        """Stand in for the Lua that composes the key from the generations.

        Reimplemented rather than mocked: composing the key IS the behaviour
        these tests are about, so a stub returning a fixed key would assert
        nothing. Kept deliberately close to the script it mirrors.
        """
        def run(keys, args):
            parts = [args[0]]
            parts += [self.values.get(k, "0") for k in keys]
            parts.append(args[1])
            key = ":".join(parts)
            return [key, self.get(key)]

        return run

    def incr(self, key):
        self.values[key] = str(int(self.values.get(key, "0")) + 1)
        return int(self.values[key])

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)

    def scan_iter(self, match=None, count=None):
        prefix = (match or "").rstrip("*")
        return [k for k in list(self.values) if k.startswith(prefix)]

    def ping(self):
        return True

    def flushdb(self):
        self.values.clear()


@pytest.fixture
def cache(monkeypatch):
    """A live cache over an in-memory client."""
    client = cache_module.CacheClient.__new__(cache_module.CacheClient)
    client.enabled = True
    client.client = FakeRedis()
    client._lookup_script = client.client.register_script(cache_module.LOOKUP_SCRIPT)
    monkeypatch.setattr(cache_module, "_cache", client)
    return client


# ---------------------------------------------------------------------------
# The decorator
# ---------------------------------------------------------------------------


def test_a_repeated_call_is_not_recomputed(cache):
    calls = []

    @cache_module.cached("users")
    def roles(username):
        calls.append(username)
        return ["editor"]

    assert roles("alice") == ["editor"]
    assert roles("alice") == ["editor"]
    assert calls == ["alice"]


def test_different_arguments_are_different_entries(cache):
    calls = []

    @cache_module.cached("users")
    def roles(username):
        calls.append(username)
        return [username]

    roles("alice")
    roles("bob")
    assert calls == ["alice", "bob"]


def test_false_is_cached_but_none_is_not(cache):
    """`False` is an answer; `None` is indistinguishable from a miss.

    Storing `None` would produce a hit path that recomputes on every call while
    looking like it works - so it is not stored, and that is deliberate rather
    than an oversight to be "fixed" later.
    """
    answers = iter([False, None, None])
    calls = []

    @cache_module.cached("users")
    def answer(_arg):
        calls.append(1)
        return next(answers)

    assert answer("denied") is False
    assert answer("denied") is False
    assert len(calls) == 1

    assert answer("missing") is None
    assert answer("missing") is None
    assert len(calls) == 3


def test_a_declared_collection_is_required():
    with pytest.raises(ValueError):
        @cache_module.cached()
        def anything():
            return 1


# ---------------------------------------------------------------------------
# Invalidation by generation
# ---------------------------------------------------------------------------


def test_writing_the_collection_invalidates_the_entry(cache):
    stored = {"roles": ["editor"]}
    calls = []

    @cache_module.cached("users")
    def roles():
        calls.append(1)
        return list(stored["roles"])

    assert roles() == ["editor"]
    stored["roles"] = ["admin"]
    assert roles() == ["editor"], "still the cached answer, as expected"

    cache_module.bump("users")

    assert roles() == ["admin"]
    assert len(calls) == 2


def test_writing_an_UNDECLARED_collection_does_not_invalidate(cache):
    """The declaration is load-bearing, not documentation.

    A function that reads a collection it does not declare keeps answering from
    before that collection changed - which is why `has_role` declares `system`
    as well as `users`.
    """
    calls = []

    @cache_module.cached("users")
    def roles():
        calls.append(1)
        return ["editor"]

    roles()
    cache_module.bump("post_types")
    roles()

    assert calls == [1], "an undeclared collection moving did not invalidate"


def test_every_declared_collection_invalidates_independently(cache):
    calls = []

    @cache_module.cached("users", "system")
    def decide():
        calls.append(1)
        return True

    decide()
    cache_module.bump("system")
    decide()
    cache_module.bump("users")
    decide()

    assert len(calls) == 3


def test_clearing_the_cache_does_not_flush_the_broker(cache):
    """The cache shares a Redis database with the Celery broker, so the
    operator-facing clear deletes by prefix. A FLUSHDB here would discard
    queued reindexing, file processing and every plugin bulk action.
    """
    cache.client.values["celery-task-meta-abc"] = "a queued job"

    @cache_module.cached("users")
    def roles():
        return ["editor"]

    roles()
    cache.clear_cache()

    assert cache.client.values.get("celery-task-meta-abc") == "a queued job"
    assert not [k for k in cache.client.values if k.startswith(cache_module.KEY_PREFIX)]


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


def test_redis_being_down_does_not_break_the_read(cache, monkeypatch):
    """A cache that takes the application down with it is worse than no cache."""
    def unavailable(*a, **k):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(cache, "_lookup_script", unavailable)

    @cache_module.cached("users")
    def roles():
        return ["editor"]

    assert roles() == ["editor"]


def test_a_write_still_succeeds_when_the_cache_cannot_be_invalidated(cache, monkeypatch):
    """Refusing a legitimate write because Redis is unavailable trades a real
    failure for a bounded staleness the TTL already covers."""
    monkeypatch.setattr(
        cache.client, "incr", lambda *a, **k: (_ for _ in ()).throw(ConnectionError())
    )

    cache_module.bump("users")  # must not raise


def test_disabled_means_a_straight_call(monkeypatch):
    client = cache_module.CacheClient.__new__(cache_module.CacheClient)
    client.enabled = False
    client.client = FakeRedis()
    client._lookup_script = client.client.register_script(cache_module.LOOKUP_SCRIPT)
    monkeypatch.setattr(cache_module, "_cache", client)

    calls = []

    @cache_module.cached("users")
    def roles():
        calls.append(1)
        return ["editor"]

    roles()
    roles()
    assert len(calls) == 2
    assert client.client.values == {}


# ---------------------------------------------------------------------------
# Keys are shared between processes
# ---------------------------------------------------------------------------


def test_the_key_is_stable_across_interpreters():
    """The web process and a worker must compute the same key for the same
    call, so the fingerprint cannot use `hash()` - it is salted per interpreter.
    """
    import subprocess
    import sys

    program = (
        "import sys; sys.path.insert(0, '.');"
        "from archihub.infra.cache import _fingerprint;"
        "print(_fingerprint(len, ('alice', 'admin'), {}))"
    )
    runs = {
        subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True, text=True, check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        ).stdout.strip()
        for seed in ("0", "1", "12345")
    }

    assert len(runs) == 1, f"the fingerprint moved with PYTHONHASHSEED: {runs}"


# ---------------------------------------------------------------------------
# The structural guarantee
# ---------------------------------------------------------------------------


WRITE_METHODS = {
    "insert_record": ("things", {"a": 1}),
    "insert_records": ("things", [{"a": 1}]),
    "update_record": ("things", {"_id": 1}, {"a": 2}),
    "upsert_record": ("things", {"_id": 1}, {"a": 2}),
    "update_records": ("things", {"a": 1}, {"a": 2}),
    "update_record_operator": ("things", {"_id": 1}, {"$set": {"a": 2}}),
    "increment_record": ("things", {"_id": 1}, "n", 1),
    "delete_record": ("things", {"_id": 1}),
    "delete_records": ("things", {"a": 1}),
}


def test_every_mongo_write_method_invalidates_its_collection(monkeypatch):
    """The one place invalidation happens, asserted over ALL of it.

    Enumerated from the class rather than from this list, so a write method
    added later fails here until it invalidates - which is the entire reason
    invalidation lives in the wrapper instead of at the call sites.
    """
    from archihub.infra import mongo as mongo_module

    class FakeCollection:
        def insert_one(self, *a, **k): return None
        def insert_many(self, *a, **k): return None
        def update_one(self, *a, **k): return None
        def update_many(self, *a, **k): return None
        def delete_one(self, *a, **k): return None
        def delete_many(self, *a, **k): return None

    wrapper = mongo_module.MongoClientWrapper.__new__(mongo_module.MongoClientWrapper)
    wrapper.db = {"things": FakeCollection()}

    declared = {
        name for name, value in vars(mongo_module.MongoClientWrapper).items()
        if callable(value) and not name.startswith("_")
        and name.split("_")[0] in {"insert", "update", "upsert", "increment", "delete"}
    }
    assert declared == set(WRITE_METHODS), (
        "a write method was added or renamed; give it a case here and make sure "
        f"it invalidates: {declared ^ set(WRITE_METHODS)}"
    )

    for name, call in WRITE_METHODS.items():
        bumped: list[str] = []
        monkeypatch.setattr(mongo_module, "_invalidate", lambda c, _b=bumped: _b.append(c))
        getattr(wrapper, name)(*call)
        assert bumped == ["things"], f"{name} did not invalidate its collection"


def test_a_failed_bulk_insert_still_invalidates(monkeypatch):
    """A partially applied batch has changed the collection, so a raised
    BulkWriteError must not leave the cache describing the state before it."""
    from archihub.infra import mongo as mongo_module

    class Exploding:
        def insert_many(self, *a, **k):
            raise RuntimeError("partial failure")

    wrapper = mongo_module.MongoClientWrapper.__new__(mongo_module.MongoClientWrapper)
    wrapper.db = {"things": Exploding()}

    bumped: list[str] = []
    monkeypatch.setattr(mongo_module, "_invalidate", lambda c: bumped.append(c))

    with pytest.raises(RuntimeError):
        wrapper.insert_records("things", [{"a": 1}])

    assert bumped == ["things"]


#: What each cached function depends on, and why. Pinned rather than derived:
#: a declaration can only be checked automatically for collections the function
#: reads DIRECTLY, and the interesting dependencies are the ones reached through
#: a helper - which is exactly where an under-declaration hides.
DECLARED = {
    ("archihub.api.users.services", "has_role"): ("users", "system"),
    ("archihub.api.users.services", "has_right"): ("users", "system"),
    ("archihub.core.roles", "get_roles"): ("system", "lists"),
    ("archihub.core.roles", "get_access_rights"): ("system", "lists"),
    ("archihub.api.system.storage", "catalogued_files"): ("records",),
}


@pytest.mark.parametrize(("location", "expected"), sorted(DECLARED.items()))
def test_a_cached_function_declares_every_collection_it_depends_on(location, expected):
    """Narrowing a declaration is how a stale authorisation decision gets served.

    `has_role` reads the account document, and it also consults the
    scheduled-task configuration in `system` through `_is_valid_system_user`.
    Dropping `system` here would leave it answering from before a scheduled task
    was removed, with nothing to show for it.
    """
    import importlib

    module_name, function_name = location
    function = getattr(importlib.import_module(module_name), function_name)

    assert getattr(function, "cache_collections", None) == expected


def test_every_cached_function_in_the_package_is_pinned_above():
    """So a new one cannot be added without stating what it depends on."""
    import ast
    import pathlib

    package = pathlib.Path(cache_module.__file__).parent.parent
    found = set()
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        module = str(path.relative_to(package.parent).with_suffix("")).replace("/", ".")
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                name = decorator.func if isinstance(decorator, ast.Call) else decorator
                if getattr(name, "id", None) == "cached":
                    found.add((module, node.name))

    assert found == set(DECLARED), f"unpinned or stale: {found ^ set(DECLARED)}"
