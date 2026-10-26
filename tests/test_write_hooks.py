"""The hook bus is not empty in the process that raises the events.

WHAT THIS GUARDS. Hooks are how a write fans out: creating a resource queues its
indexing, attaching files queues their processing. Registration is process-local,
and the process that *raises* these events is the web process, so both the
plugin registrations and the core indexing ones must exist there too.

Neither absence produces an error. `hooks.call()` on a name with no registrations
returns its argument and does nothing, so the API answers 201, the operator sees
a saved resource, and the derivative is never made and the index never updated.
That is the exact failure shape a status assertion cannot see, which is why the
guard is structural: assert the registrations exist, not that a request succeeds.

Two claims are checked by reading `create_app`'s source rather than by calling
it, for the reason `test_celery_binding.py` gives: building the application reads
the active plugin list from Mongo, and this suite runs with no infrastructure.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from archihub.core.hooks import HookHandler, get_hook_handler

APP_FACTORY = pathlib.Path(__file__).resolve().parent.parent / "archihub" / "core" / "app_factory.py"
WORKER = pathlib.Path(__file__).resolve().parent.parent / "archihub" / "worker" / "celery_app.py"


@pytest.fixture
def hooks():
    """A clean hook bus. It is a process-wide singleton, so it must be reset."""
    handler = get_hook_handler()
    handler.unregister_all()
    yield handler
    handler.unregister_all()


def _direct_calls(node: ast.AST) -> set[str]:
    """Plain function names called anywhere under ``node``."""
    return {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }


def _reachable_calls(source: str, root: str) -> set[str]:
    """Names reachable from ``root`` through module-local functions.

    Scanning the whole module instead would accept a call that is still written
    down but no longer reached - which is exactly what happened while this file
    was being written: deleting `_register_index_hooks()` from `create_app` left
    the helper defining it in place, and a flat scan still found the name inside
    the orphan. A guard that cannot fail is not a guard, so this walks the call
    graph and answers the question that matters: does building the application
    actually get here?
    """
    tree = ast.parse(source)
    edges = {
        node.name: _direct_calls(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    seen: set[str] = set()
    frontier = [root]
    while frontier:
        name = frontier.pop()
        for called in edges.get(name, ()):
            if called not in seen:
                seen.add(called)
                frontier.append(called)
    return seen


# ---------------------------------------------------------------------------
# The web process registers plugin hooks
# ---------------------------------------------------------------------------


def test_the_app_factory_activates_plugin_settings():
    """Without this the web process mounts plugin ROUTES and no plugin HOOKS.

    Automatic processing must be registered in every process that raises its
    events.
    """
    assert "activate_plugin_settings" in _reachable_calls(APP_FACTORY.read_text(), "create_app"), (
        "archihub/core/app_factory.py must call activate_plugin_settings(). "
        "Without it no plugin hook is registered in the web process, so "
        "attaching files to a resource returns 201 and processes nothing."
    )


def test_the_worker_activates_plugin_settings_too():
    """A worker raises `resource_update` of its own, through plugins.framework.data."""
    assert "activate_plugin_settings" in _reachable_calls(WORKER.read_text(), "_load_plugins")


# ---------------------------------------------------------------------------
# Both processes register the indexing hooks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module", "root"),
    [(APP_FACTORY, "create_app"), (WORKER, "_load_plugins")],
    ids=["app_factory", "worker"],
)
def test_both_processes_register_the_index_hooks(module, root):
    assert "register_index_hooks" in _reachable_calls(module.read_text(), root), (
        f"{module.name} must reach register_index_hooks() from {root}(); a write "
        "that does not reach the index leaves search answering with stale data, "
        "silently."
    )


def test_indexing_registers_against_every_write_event(monkeypatch, hooks):
    from archihub.api.search import write_hooks

    monkeypatch.setattr("archihub.api.search.services.indexing_enabled", lambda: True)
    write_hooks.register_index_hooks()

    for event in ("resource_create", "resource_update", "resource_delete"):
        assert hooks.hooks.get(event), f"{event} has no indexing registration"


def test_indexing_runs_after_the_plugins_that_rewrite_metadata(monkeypatch, hooks):
    """Queue 101 is above every plugin's configurable order, and must stay there.

    A plugin that fills in a field has to finish before the document is built,
    or the resource is indexed without the value it just gained.
    """
    from archihub.api.search import write_hooks

    monkeypatch.setattr("archihub.api.search.services.indexing_enabled", lambda: True)
    write_hooks.register_index_hooks()

    queues = {queue for queue, *_ in hooks.hooks["resource_update"]}
    assert queues == {write_hooks.INDEX_QUEUE}
    assert write_hooks.INDEX_QUEUE > 100


def test_nothing_is_registered_when_indexing_is_off(monkeypatch, hooks):
    """An instance with no Elasticsearch must not queue a job per write."""
    from archihub.api.search import write_hooks

    monkeypatch.setattr("archihub.api.search.services.indexing_enabled", lambda: False)
    write_hooks.register_index_hooks()

    assert hooks.hooks == {}


def test_an_unreadable_setting_does_not_stop_the_backend_coming_up(monkeypatch, hooks):
    """A stale index is bad; an archive that will not start is worse."""
    from archihub.api.search import write_hooks

    def explode():
        raise RuntimeError("mongo is down")

    monkeypatch.setattr("archihub.api.search.services.indexing_enabled", explode)
    write_hooks.register_index_hooks()

    assert hooks.hooks == {}


def test_registering_twice_does_not_double_the_indexing(monkeypatch, hooks):
    """Both the mount path and a reload may call this; the bus de-duplicates."""
    from archihub.api.search import write_hooks

    monkeypatch.setattr("archihub.api.search.services.indexing_enabled", lambda: True)
    write_hooks.register_index_hooks()
    write_hooks.register_index_hooks()

    assert len(hooks.hooks["resource_create"]) == 1



def test_a_restored_resource_goes_back_into_the_index(hooks, monkeypatch):
    """Deleting a resource takes it out of the index; restoring must put it back."""
    from archihub.api.search import write_hooks
    from archihub.worker.tasks.indexing import index_resources_task

    monkeypatch.setattr("archihub.api.search.services.indexing_enabled", lambda: True)
    write_hooks.register_index_hooks()

    assert [func for _q, func, _a, _k in hooks.hooks["resource_restore"]] == [index_resources_task]


def test_deleting_a_content_type_takes_its_resources_out_of_the_index(hooks, monkeypatch):
    from archihub.api.search import write_hooks
    from archihub.worker.tasks.indexing import index_resources_delete_by_type_task

    monkeypatch.setattr("archihub.api.search.services.indexing_enabled", lambda: True)
    write_hooks.register_index_hooks()

    assert [func for _q, func, _a, _k in hooks.hooks["type_delete"]] == [
        index_resources_delete_by_type_task
    ]


def _resource_hook_bodies() -> list[tuple[str, int, str, set[str]]]:
    """Every literal body a `resource*` hook is fired with under `api/resources`.

    A body built by spreading another dict cannot be read statically and is
    left to the unit tests of the function that builds it.
    """
    found = []
    root = pathlib.Path(__file__).resolve().parent.parent / "archihub" / "api" / "resources"
    for path in sorted(root.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "_call_hook" or len(node.args) < 2:
                continue
            name, body = node.args[0], node.args[1]
            if not (isinstance(name, ast.Constant) and str(name.value).startswith("resource")):
                continue
            if not isinstance(body, ast.Dict) or None in body.keys:
                continue
            keys = {key.value for key in body.keys if isinstance(key, ast.Constant)}
            found.append((path.name, node.lineno, name.value, keys))
    return found


def test_every_resource_hook_states_the_content_type():
    """Automatic subscribers compare `post_type` against their configuration
    before doing anything, so a body without it turns every one of them into a
    no-op - whichever hook an operator chose to attach them to."""
    bodies = _resource_hook_bodies()
    assert bodies, "found no resource hook calls to check"

    missing = [f"{name}:{line} {hook}" for name, line, hook, keys in bodies if "post_type" not in keys]
    assert not missing, f"resource hooks fired without post_type: {missing}"


# ---------------------------------------------------------------------------
# The body a hook is fired with
# ---------------------------------------------------------------------------


def _files_create_body() -> dict:
    """The literal dict `resources.write.create` fires `resource_files_create` with."""
    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "archihub" / "api" / "resources" / "write.py"
    ).read_text()

    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "_call_hook" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and first.value == "resource_files_create":
            return {
                key.value: True
                for key in node.args[1].keys
                if isinstance(key, ast.Constant)
            }

    pytest.fail("resources/write.py no longer fires resource_files_create")


def test_the_files_create_hook_states_the_content_type():
    """Every subscriber's first line compares `post_type` against its own config.

    Omit it and the comparison fails for every configured type, so each hook
    returns on entry - which is indistinguishable, from outside, from a plugin
    that ran and had nothing to do.
    """
    body = _files_create_body()

    assert "post_type" in body, (
        "resource_files_create must carry post_type; without it every automatic "
        "processing hook exits before doing any work."
    )
    assert "_id" in body, "a subscriber needs the resource whose files these are"


def test_a_subscriber_matching_no_type_is_what_an_absent_post_type_looks_like():
    """Demonstrates the failure the assertion above prevents."""
    from archihub.plugins.filesProcessing import automatic_task

    assert automatic_task({"type": "archivo"}, {"filesObj": [], "_id": "x"}) == "ok"


def test_the_bus_dispatches_a_registered_task_rather_than_running_it(hooks):
    """A registration in the web process queues; it does not execute inline.

    This is the property that makes registering here correct, and the one the
    docstring it replaced got backwards.
    """
    sent: list = []

    class FakeTask:
        name = "fake.task"

        def si(self, *args, **kwargs):
            sent.append((args, kwargs))
            return self

    monkeypatched = FakeTask()
    hooks.register("resource_create", monkeypatched, queue=5)

    original = HookHandler._dispatch_chain
    try:
        HookHandler._dispatch_chain = lambda self, *a, **k: None
        hooks.call("resource_create", {"_id": "abc"})
    finally:
        HookHandler._dispatch_chain = original

    assert sent == [(({"_id": "abc"},), {})]
