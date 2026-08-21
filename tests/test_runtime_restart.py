"""Restarting every process of a deployment.

Some settings are read once, at startup - which plugins are active decides what
is mounted and which Celery tasks are registered. A deployment is several
processes and possibly several machines, and the request to change one of those
settings arrives at exactly one of them, so "restart" has to travel through the
only thing they all share: the database.

WHAT THESE TESTS ASSERT IS THE WIRING, NOT THE TERMINATION. Letting the real
functions run would send SIGTERM to the process running the suite - the autouse
fixture in conftest neutralises them for exactly that reason - so each test here
checks that the service reaches the machinery, and the machinery is exercised
against its own fakes.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from archihub.core import runtime_restart


# ---------------------------------------------------------------------------
# The revision counter
# ---------------------------------------------------------------------------


class _FakeMongo:
    def __init__(self, record=None):
        self.record = record
        self.updates: list[tuple] = []
        self.inserts: list[tuple] = []

    def get_record(self, collection, filters=None, fields=None):
        return self.record

    def update_record_operator(self, collection, filters, operator, **kwargs):
        self.updates.append((collection, filters, operator))

    def insert_record(self, collection, record):
        self.inserts.append((collection, record))


@pytest.fixture
def mongo(monkeypatch):
    fake = _FakeMongo()
    import archihub.infra.mongo as mongo_module

    monkeypatch.setattr(mongo_module, "get_mongo", lambda: fake)
    return fake


def _value(payload: list[dict], field_id: str):
    return next(item["value"] for item in payload if item["id"] == field_id)


def test_the_first_request_creates_the_control_document(mongo):
    mongo.record = None

    revision = runtime_restart.request_runtime_restart("manual_restart")

    assert revision == 1
    assert not mongo.updates
    (_collection, record), = mongo.inserts
    assert record["name"] == runtime_restart.RESTART_CONTROL_OPTION
    assert _value(record["data"], runtime_restart.RESTART_REVISION_ID) == 1
    assert _value(record["data"], runtime_restart.RESTART_REASON_ID) == "manual_restart"


def test_a_later_request_increments_the_existing_revision(mongo):
    mongo.record = {
        "name": runtime_restart.RESTART_CONTROL_OPTION,
        "data": [{"id": runtime_restart.RESTART_REVISION_ID, "value": 4}],
    }

    assert runtime_restart.request_runtime_restart("plugin_status_updated") == 5

    (_collection, _filters, operator), = mongo.updates
    payload = operator["$set"]["data"]
    assert _value(payload, runtime_restart.RESTART_REVISION_ID) == 5
    assert _value(payload, runtime_restart.RESTART_REASON_ID) == "plugin_status_updated"


def test_unrelated_fields_in_the_document_survive(mongo):
    """The document is shared, so a restart request must not drop what it holds."""
    mongo.record = {
        "name": runtime_restart.RESTART_CONTROL_OPTION,
        "data": [
            {"id": runtime_restart.RESTART_REVISION_ID, "value": 1},
            {"id": "something_else", "value": "keep me"},
        ],
    }

    runtime_restart.request_runtime_restart()

    (_collection, _filters, operator), = mongo.updates
    assert _value(operator["$set"]["data"], "something_else") == "keep me"


def test_an_unreadable_revision_reads_as_zero(mongo):
    """A malformed value must not make the counter unusable.

    Reading it as zero means the next request writes 1, which every process sees
    as a change - a spurious restart, where raising would leave the deployment
    with no way to restart at all.
    """
    mongo.record = {"name": "runtime_control", "data": [{"id": "restart_revision", "value": "x"}]}
    assert runtime_restart.get_restart_revision() == 0


# ---------------------------------------------------------------------------
# Choosing what to signal
# ---------------------------------------------------------------------------


def test_a_supervisor_at_pid_1_is_sent_sighup(monkeypatch):
    """SIGHUP is what the supervisor traps and answers by starting the child again."""
    import signal

    monkeypatch.delenv("ARCHIHUB_RESTART_SIGNAL", raising=False)
    monkeypatch.setattr(runtime_restart, "_process_cmdline", lambda pid: ["/bin/bash", "/app/start.sh"])
    assert runtime_restart._restart_signal(1) == signal.SIGHUP


def test_anything_else_at_pid_1_is_sent_sigterm(monkeypatch):
    """SIGHUP to a process that is not the supervisor is not a restart.

    Nothing would bring it back - the default disposition simply terminates it.
    """
    import signal

    monkeypatch.delenv("ARCHIHUB_RESTART_SIGNAL", raising=False)
    monkeypatch.setattr(runtime_restart, "_process_cmdline", lambda pid: ["/usr/bin/tini", "--"])
    assert runtime_restart._restart_signal(1) == signal.SIGTERM


def test_the_poll_interval_has_a_floor(monkeypatch):
    """This runs in every process, so a mistyped 0 would be a load generator."""
    monkeypatch.setenv("ARCHIHUB_RESTART_POLL_INTERVAL", "0")
    assert runtime_restart.get_restart_poll_interval() == 1.0

    monkeypatch.setenv("ARCHIHUB_RESTART_POLL_INTERVAL", "not-a-number")
    assert runtime_restart.get_restart_poll_interval() == 5.0


@pytest.fixture
def signals(monkeypatch):
    """Record what would be signalled, instead of signalling it."""
    sent = []
    monkeypatch.setattr(runtime_restart.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.delenv("ARCHIHUB_RESTART_SIGNAL", raising=False)
    monkeypatch.delenv("ARCHIHUB_RESTART_SIGNAL_PID", raising=False)
    # The autouse fixture in conftest replaces this for the whole suite; these
    # tests are the ones that must run the real thing, against a recorded kill.
    monkeypatch.setattr(
        runtime_restart, "terminate_runtime", runtime_restart._real_terminate_runtime
    )
    return sent


def test_a_supervisor_at_pid_1_is_signalled(signals, monkeypatch):
    import signal as signalmod

    monkeypatch.setattr(runtime_restart, "_process_cmdline", lambda pid: ["/bin/bash", "/app/start.sh"])
    runtime_restart.terminate_runtime()

    assert signals == [(1, signalmod.SIGHUP)]


def test_a_pid_1_that_is_not_the_supervisor_is_never_signalled(signals, monkeypatch):
    """The one process that must not be signalled speculatively.

    In a container PID 1 is the entrypoint, so a signal there stops everything
    rather than restarting one part of it; on a developer's machine it is the
    system init, which will not restart anything either. Only this process is
    stopped, and whatever launched it decides what happens next.
    """
    import os as osmod

    monkeypatch.setattr(runtime_restart, "_process_cmdline", lambda pid: ["/sbin/init"])
    runtime_restart.terminate_runtime()

    assert [pid for pid, _ in signals] == [osmod.getpid()]
    assert 1 not in [pid for pid, _ in signals]


def test_an_explicitly_named_supervisor_pid_is_trusted(signals, monkeypatch):
    """Naming a pid is a statement about that deployment's process tree."""
    monkeypatch.setenv("ARCHIHUB_RESTART_SIGNAL_PID", "4242")
    monkeypatch.setattr(runtime_restart, "_process_cmdline", lambda pid: ["something-unrecognised"])

    runtime_restart.terminate_runtime()

    assert [pid for pid, _ in signals] == [4242]


def test_nothing_here_ever_starts_a_process():
    """A process that stops without a supervisor stays stopped, on purpose.

    Restarting itself detaches the replacement from the terminal that started
    it, leaving an orphan the operator cannot see or stop and a second worker
    competing for the same queue under the same node name. Every deployment has
    something that restarts it - the entrypoint scripts are loops and the
    containers carry `restart: always` - so this module never needs to.
    """
    import ast
    import pathlib as pathlib_

    tree = ast.parse(pathlib_.Path("archihub/core/runtime_restart.py").read_text())
    spawners = {"Popen", "run", "system", "fork", "execv", "execvp", "spawnv"}
    found = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in spawners
    ]
    assert found == [], f"this module must not start processes, found: {found}"


# ---------------------------------------------------------------------------
# The wiring
# ---------------------------------------------------------------------------


def _function_source(path: str, name: str) -> str:
    """The source text of one top-level function, straight from the file."""
    module = ast.parse(pathlib.Path(path).read_text())
    node = next(
        n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == name
    )
    return ast.get_source_segment(pathlib.Path(path).read_text(), node) or ""


def _calls_in(func) -> set[str]:
    """Every function name called anywhere in ``func``'s body."""
    tree = ast.parse(inspect.getsource(func).lstrip())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def test_the_manual_route_records_the_request_and_stops_the_process():
    from archihub.api.system import services

    calls = _calls_in(services.restart_system)
    assert "request_runtime_restart" in calls
    # Recording alone changes nothing in THIS process - it polls for the change
    # like every other one, so without this the operator waits a poll interval
    # for the container they are looking at.
    assert "schedule_local_restart" in calls


def test_changing_a_plugins_activation_requests_a_restart():
    """Otherwise the plugin the operator just enabled appears to do nothing.

    What is mounted and which tasks are registered are both decided at startup,
    so the setting is written and nothing observable changes.
    """
    from archihub.api.system import services

    assert "_request_restart" in _calls_in(services.set_plugin_active)


def test_the_web_process_watches_for_restarts_requested_elsewhere():
    from archihub.core import app_factory

    assert "_start_restart_monitor" in _calls_in(app_factory.create_app)
    assert "start_runtime_restart_monitor" in _calls_in(app_factory._start_restart_monitor)


def test_the_worker_watches_too():
    """A worker holds its own task registry and hook registrations.

    A plugin toggled in the admin screen reaches it only through this.
    """
    from archihub.worker import celery_app as worker

    assert "start_runtime_restart_monitor" in _calls_in(worker._init_worker)


def test_the_monitor_does_not_read_the_database_before_starting_its_thread():
    """Startup must not wait on a database for a background facility.

    Where the database is unreachable, a read here would block every process for
    the whole connection timeout before it could serve anything.
    """
    # Read from the file, not through the module attribute: conftest replaces
    # that attribute with a stub for the whole suite, and inspecting the stub
    # would make this pass no matter what the real function does.
    source = _function_source("archihub/core/runtime_restart.py", "start_runtime_restart_monitor")
    outer, _, inner = source.partition("def _monitor(")
    assert "get_restart_revision" not in outer
    assert "get_restart_revision" in inner


def test_the_settings_screen_button_reaches_a_real_route():
    """The shipped settings document renders a button that GETs /system/<value>.

    The button and the route are separate files, so nothing else would notice
    them disagreeing - the operator would simply get a 404.
    """
    from archihub.api.system.default_settings import settings as shipped
    from archihub.core.app_factory import create_app
    from archihub.core.routing import iter_api_routes

    restart_setting = next(s for s in shipped if s["name"] == "system_restart")
    action = restart_setting["data"][0]["value"]

    paths = {path for path, _route in iter_api_routes(create_app())}
    assert f"/system/{action}" in paths


def test_no_route_module_terminates_the_process_itself():
    """Stopping the process belongs in one place.

    A handler that sends the signal itself would kill the worker before its
    response was written, and would be invisible to the fixture that keeps the
    test suite alive.
    """
    offenders = []
    for path in pathlib.Path("archihub/api").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "kill"
            ):
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == []
