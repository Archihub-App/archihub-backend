"""The hook bus.

Hooks are how side effects fan out (indexing, vectorisation, plugin
post-processing) without domain modules importing each other, so their ordering
and de-duplication semantics are load-bearing well beyond this module.
"""

from __future__ import annotations

import pytest

from archihub.core.hooks import HookHandler


@pytest.fixture
def hooks() -> HookHandler:
    handler = HookHandler()
    handler.unregister_all()
    yield handler
    handler.unregister_all()


class FakeTask:
    """Stands in for a Celery task: what matters is exposing `.si()`."""

    def __init__(self, name: str, recorder: list):
        self.name = name
        self._recorder = recorder

    def si(self, *args, **kwargs):
        self._recorder.append((self.name, args, kwargs))
        return f"signature:{self.name}"


def test_singleton_identity():
    assert HookHandler() is HookHandler()


def test_sync_callback_runs_inline(hooks):
    calls = []
    hooks.register("thing_created", lambda payload: calls.append(payload))
    hooks.call("thing_created", {"id": 1})
    assert calls == [{"id": 1}]


def test_callbacks_run_in_queue_order(hooks):
    """Ascending `queue`, and it matters.

    Built-in indexing registers at 101 (Elasticsearch) and 102 (Qdrant) so that
    plugins registering at the default 0 can modify a resource BEFORE it is
    indexed. Reordering would index stale data.
    """
    order = []
    hooks.register("resource_create", lambda p: order.append("qdrant"), queue=102)
    hooks.register("resource_create", lambda p: order.append("plugin"), queue=0)
    hooks.register("resource_create", lambda p: order.append("elastic"), queue=101)

    hooks.call("resource_create", {})
    assert order == ["plugin", "elastic", "qdrant"]


def test_sync_callbacks_do_not_chain_into_each_other(hooks):
    """Each callback gets the ORIGINAL arguments; the last return value wins.

    Easy to misread as a pipeline, and it is not one: the second callback below
    receives 1, not the first callback's output of 2. So the result is 1*10, not
    (1+1)*10. Preserved deliberately - a plugin registering a hook must be able
    to assume it sees the caller's payload, not some other plugin's rewrite of it.
    """
    seen = []
    hooks.register("validate_field", lambda value: (seen.append(value), value + 1)[1], queue=1)
    hooks.register("validate_field", lambda value: (seen.append(value), value * 10)[1], queue=2)

    assert hooks.call("validate_field", 1) == 10
    assert seen == [1, 1]


def test_unregistered_hook_returns_the_input(hooks):
    """A hook nobody listens to must not swallow the payload.

    Callers use the return value as the (possibly transformed) payload, so
    returning None here would blank out data whenever no plugin is installed.
    """
    assert hooks.call("nobody_listens", {"a": 1}) == {"a": 1}
    assert hooks.call("nobody_listens") is None


def test_duplicate_registration_is_ignored(hooks):
    calls = []

    def callback(payload):
        calls.append(payload)

    hooks.register("x", callback)
    hooks.register("x", callback)
    hooks.call("x", 1)
    assert len(calls) == 1


def test_duplicate_bound_method_from_a_new_instance_is_ignored(hooks):
    """Re-instantiating a plugin must not double its side effects.

    filesProcessing constructs a fresh instance of itself inside its Celery
    tasks, and `obj.method == other_obj.method` is False - so without the
    bound-method comparison every task run would add another registration and
    the effect would compound.
    """
    calls = []

    class Plugin:
        def on_event(self, payload):
            calls.append(payload)

    hooks.register("evt", Plugin().on_event)
    hooks.register("evt", Plugin().on_event)

    hooks.call("evt", 1)
    assert len(calls) == 1


def test_registration_failure_is_not_silent(hooks):
    """register() must not swallow errors.

    The original wrapped its whole body in `except Exception: print(...)`, so a
    hook that failed to register produced a feature that silently never ran.
    """
    with pytest.raises((TypeError, ValueError)):
        hooks.register("evt", lambda: None, kwargs=["not", "a", "dict"])


def test_failing_sync_callback_propagates(hooks):
    """A sync hook runs in the caller's request path, so its failure is theirs."""

    def explode(payload):
        raise ValueError("boom")

    hooks.register("evt", explode)
    with pytest.raises(ValueError):
        hooks.call("evt", {})


# ---------------------------------------------------------------------------
# Celery-task callbacks
# ---------------------------------------------------------------------------


def test_celery_tasks_are_chained_not_run_inline(hooks, monkeypatch):
    built = []
    recorded = []

    monkeypatch.setattr(
        "archihub.core.hooks.chain",
        lambda *sigs: type("R", (), {"apply_async": lambda self: None})(),
    )
    monkeypatch.setattr("archihub.api.tasks.services.add_task", lambda *a, **k: recorded.append(a))

    hooks.register("resource_create", FakeTask("index.resource", built))
    hooks.call("resource_create", {"id": 7})

    assert built == [("index.resource", ({"id": 7},), {})]


def test_celery_results_are_not_part_of_the_sync_pipeline(hooks, monkeypatch):
    """Task callbacks are fire-and-forget; only sync ones thread a value."""
    monkeypatch.setattr(
        "archihub.core.hooks.chain",
        lambda *sigs: type("R", (), {"apply_async": lambda self: None})(),
    )
    monkeypatch.setattr("archihub.api.tasks.services.add_task", lambda *a, **k: None)

    hooks.register("evt", FakeTask("some.task", []), queue=1)
    hooks.register("evt", lambda value: "from-sync", queue=2)

    assert hooks.call("evt", "input") == "from-sync"


def test_broker_failure_does_not_break_the_request(hooks, monkeypatch):
    """If the broker is down, the caller's own work must still succeed.

    Queuing a background side effect is not worth failing a user's request over.
    """

    def _explode(*_sigs):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr("archihub.core.hooks.chain", _explode)
    hooks.register("evt", FakeTask("some.task", []))

    assert hooks.call("evt", "payload") == "payload"


def test_task_ids_are_paired_with_names_safely(monkeypatch):
    """Regression guard for an IndexError in the original.

    The original indexed `names[x]` by position after de-duplicating task ids,
    which misaligns as soon as an id repeats and raises IndexError when the
    chain yields more ids than names.
    """
    recorded = []
    monkeypatch.setattr(
        "archihub.api.tasks.services.add_task",
        lambda task_id, name, user, kind: recorded.append((task_id, name)),
    )

    class Result:
        def __init__(self, ident, parent=None):
            self.id = ident
            self.parent = parent

    # three chained results, but only two names
    result = Result("c", Result("b", Result("a")))
    HookHandler._register_chain_tasks(result, ["first.task", "second.task"])

    assert recorded == [("a", "first.task"), ("b", "second.task")]


def test_get_task_ids_returns_oldest_first():
    class Result:
        def __init__(self, ident, parent=None):
            self.id = ident
            self.parent = parent

    assert HookHandler.get_task_ids(Result("c", Result("b", Result("a")))) == ["a", "b", "c"]
