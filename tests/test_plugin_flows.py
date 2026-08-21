"""flowsManager: flow validation, and the gate that separates tools from flows.

The plugin's routes queue work or write, so the diff harness cannot fire at them
- the two backends share one database and a case would apply the write twice.
Coverage is therefore the pure functions the task and route bodies delegate to,
which is also where the defects live.
"""

from __future__ import annotations

import pytest

flows = pytest.importorskip("archihub.plugins.flowsManager")
flow_services = pytest.importorskip("archihub.plugins.flowsManager.services")


# ---------------------------------------------------------------------------
# Graph validation
# ---------------------------------------------------------------------------


NODES = {"llm_processing": {}, "join": {}}


def test_a_graph_whose_node_is_not_installed_is_refused():
    """The message names the type, because the operator's next step is to
    install the plugin providing it."""
    ok, message, details = flows.validate_flow_nodes(
        {"nodes": [{"type": "does_not_exist"}]}, NODES
    )

    assert ok is False
    assert "does_not_exist" in message
    assert details is None


def test_input_node_types_need_no_node_package():
    """`resource`, `record` and `string` are values the caller supplies, not
    code - requiring a package for them would make every flow unloadable."""
    for builtin in ("resource", "record", "string", "__flow_context_node__"):
        ok, message, _details = flows.validate_flow_nodes(
            {"nodes": [{"type": builtin}]}, {}
        )
        assert "not installed" not in message, f"{builtin} was treated as a node package"


def test_an_empty_graph_is_refused():
    ok, message, _d = flows.validate_flow_nodes({"nodes": []}, NODES)
    assert ok is False
    assert message


def test_a_node_with_no_type_is_refused():
    ok, message, _d = flows.validate_flow_nodes({"nodes": [{"id": "a"}]}, NODES)
    assert ok is False
    assert message


def test_a_graph_arriving_as_unparseable_text_is_refused_not_raised():
    """Flows are stored as either a dict or a JSON string, so the string path
    is real. A malformed one is a 400, not a traceback out of the route."""
    ok, message, _d = flows.validate_flow_nodes("{not json", NODES)

    assert ok is False
    assert message


# ---------------------------------------------------------------------------
# The tool gate
# ---------------------------------------------------------------------------


def test_listing_tool_flows_requires_tool_enabled_by_default(monkeypatch):
    """`is_tool_enabled` means a MODEL may call the flow on its own. The
    default must keep that gate, because the model-facing path passes no
    argument at all."""
    seen = {}

    def fake_all(collection, filters=None, **kwargs):
        seen["filters"] = filters
        return []

    monkeypatch.setattr(flow_services.mongodb, "get_all_records", fake_all)
    flow_services.get_tool_flow_records(flow_type="resource")

    assert seen["filters"].get("is_tool_enabled") is True


def test_include_disabled_drops_the_tool_gate(monkeypatch):
    """A plugin running a flow an operator named in its own settings has
    already had that decision made by a person."""
    seen = {}

    def fake_all(collection, filters=None, **kwargs):
        seen["filters"] = filters
        return []

    monkeypatch.setattr(flow_services.mongodb, "get_all_records", fake_all)
    flow_services.get_tool_flow_records(flow_type="resource", include_disabled=True)

    assert "is_tool_enabled" not in seen["filters"]


def test_the_model_facing_tool_list_never_widens(monkeypatch):
    """Whatever else changes, the tools offered TO A MODEL stay gated."""
    seen = {}

    def fake_records(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(flow_services, "get_tool_flow_records", fake_records)
    flow_services.build_flow_tools_for_model(flow_type="resource")

    assert seen.get("include_disabled") in (None, False), (
        "the model-facing tool list must not include tool-disabled flows"
    )


def test_running_a_flow_requires_tool_enabled_by_default(monkeypatch):
    """The model chooses the flow on this path, so the gate is the whole
    protection: without it a model could run any stored flow by naming it."""
    monkeypatch.setattr(
        flow_services.mongodb,
        "get_record",
        lambda collection, filters, **k: {"_id": filters["_id"], "is_tool_enabled": False},
    )

    with pytest.raises(ValueError) as excinfo:
        flow_services.run_flow_with_inputs("6a70b8c3497d4440325c94c3", {})

    assert "tool use" in str(excinfo.value)


def test_exact_type_match_requires_the_declared_type(monkeypatch):
    seen = {}

    def fake_all(collection, filters=None, **kwargs):
        seen["filters"] = filters
        return []

    monkeypatch.setattr(flow_services.mongodb, "get_all_records", fake_all)
    flow_services.get_tool_flow_records(flow_type="resource", exact_type_match=True)

    assert seen["filters"].get("type") == "resource"
    assert "$or" not in seen["filters"]


# ---------------------------------------------------------------------------
# Node discovery follows activation
# ---------------------------------------------------------------------------


def test_node_directories_skip_inactive_plugins(monkeypatch, tmp_path):
    """Activation is how an operator withdraws a plugin's code from the running
    instance. A `nodes/` directory belonging to a switched-off plugin must not
    be loadable, or the switch is cosmetic for anything reachable from a flow.
    """
    plugins_root = tmp_path / "plugins"
    for slug in ("switched_on", "switched_off"):
        (plugins_root / slug / "nodes").mkdir(parents=True)
    (plugins_root / "flowsManager" / "utils" / "nodes").mkdir(parents=True)

    monkeypatch.setattr(
        flows.os.path, "abspath", lambda _p: str(plugins_root / "flowsManager" / "__init__.py")
    )
    monkeypatch.setattr(
        "archihub.plugins.framework.discovery.get_active_plugin_slugs",
        lambda: {"flowsManager", "switched_on"},
    )

    sources = [source for _dir, _prefix, source in flows._node_directories()]

    assert "switched_on" in sources
    assert "switched_off" not in sources
