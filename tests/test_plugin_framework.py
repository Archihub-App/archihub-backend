"""The plugin framework (Phase 5).

The single most important thing asserted here is that **a plugin route's role
requirement cannot be discarded**. In the legacy framework it could, and at
all twenty-one of its call sites it was — see ``S32`` and the docstring of
``archihub/plugins/framework/base.py``. A dependency has no return value for a
handler to drop, which is why the fix is structural rather than a patch to
twenty-one call sites.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from archihub.plugins.framework import interop
from archihub.plugins.framework.base import ArchiPlugin, translate_display

PLUGINS = ("scheduleSystemTasks", "liquidText", "filesProcessing", "inventoryMaker", "massiveUpdater")


@pytest.fixture(autouse=True)
def clean_interop():
    interop.reset()
    yield
    interop.reset()


def build(slug: str):
    from archihub.plugins.framework.mounting import build_plugin

    return build_plugin(slug)


# ---------------------------------------------------------------------------
# Authorisation is a dependency, not a call
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", PLUGINS)
def test_every_plugin_route_states_a_role_requirement(slug):
    """S32. The legacy `validate_roles` returned a refusal tuple that all 21 of
    its call sites dropped, so those routes had no authorisation beyond a valid
    session - and `scheduleSystemTasks`' settings ARE a task scheduler.

    Asserted over the route's dependency list, so a handler that merely *calls*
    a check inside its body would not satisfy this.
    """
    plugin = build(slug)
    router = plugin.build()

    for route in router.routes:
        if route.path.startswith(f"/{slug}/public/"):
            # Deliberately anonymous - the public inventory download.
            continue
        names = {dep.call.__name__ for dep in route.dependant.dependencies if dep.call}
        assert "_dependency" in names, f"{route.path} declares no role dependency"


@pytest.mark.parametrize("slug", PLUGINS)
def test_no_plugin_route_handler_calls_a_role_check_itself(slug):
    """The shape that failed. A check inside a handler body has a return value,
    and a return value can be ignored.

    Over the AST rather than the text, because several of these modules *quote*
    the legacy call in a docstring while explaining why it was wrong - and a
    grep cannot tell the difference between describing a defect and having one.
    """
    import ast
    import inspect

    module = __import__(f"archihub.plugins.{slug}", fromlist=["build"])
    tree = ast.parse(inspect.getsource(module))

    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }

    assert "validate_roles" not in called


def test_an_unauthenticated_caller_is_refused_by_a_plugin_route():
    """Through the real routing stack, with no credential at all."""
    app = FastAPI()
    from archihub.core.errors import register_exception_handlers

    register_exception_handlers(app)
    app.include_router(build("liquidText").build())

    client = TestClient(app, raise_server_exceptions=False)

    assert client.post("/liquidText/bulk", json={}).status_code == 401
    assert client.get("/liquidText/settings/all").status_code == 401


# ---------------------------------------------------------------------------
# Metadata and translation
# ---------------------------------------------------------------------------


def test_only_display_keys_are_translated():
    """An id or an endpoint translated is a lookup that stops matching."""
    tree = {"id": "overwrite", "label": "Overwrite", "endpoint": "bulk", "options": [{"label": "PDF", "value": "pdf"}]}

    translated = translate_display(tree)

    assert translated["id"] == "overwrite"
    assert translated["endpoint"] == "bulk"
    assert translated["options"][0]["value"] == "pdf"


def test_translating_settings_does_not_mutate_the_plugin_info():
    """The tree is a module constant, and the legacy code assigned into it -
    `resp['settings'][1]['fields'] = [...]` - so the second request saw the
    first request's values."""
    plugin = build("filesProcessing")

    first = plugin.translated_settings()
    first["settings"][1]["fields"] = ["corrupted"]

    assert plugin.translated_settings()["settings"][1]["fields"] == []


@pytest.mark.parametrize("slug", PLUGINS)
def test_plugin_info_keeps_its_legacy_shape(slug):
    """`plugin_info` is read by the admin screens and by the beat scheduler."""
    module = __import__(f"archihub.plugins.{slug}", fromlist=["plugin_info"])
    info = module.plugin_info

    assert set(info) >= {"name", "description", "version", "author", "type", "settings"}
    assert isinstance(info["settings"], dict)


def test_the_scheduler_capability_is_declared_where_beat_looks_for_it():
    """`worker/schedule.py` reads settings only from plugins declaring it."""
    from archihub.plugins.scheduleSystemTasks import plugin_info

    assert "scheduler" in plugin_info["capabilities"]


# ---------------------------------------------------------------------------
# Stored settings
# ---------------------------------------------------------------------------


class FakeMongo:
    def __init__(self, record=None):
        self.record = record
        self.operations: list[tuple[dict, dict]] = []

    def get_record(self, collection, filters=None, fields=None):
        return self.record

    def update_record_operator(self, collection, filters, operator, **kwargs):
        self.operations.append((filters, operator))
        return None


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: fake)
    return fake


def test_settings_are_empty_rather_than_a_crash_when_nothing_is_stored(mongo):
    """The legacy version indexed a record it had not checked for existence."""
    mongo.record = None

    assert build("liquidText").get_plugin_settings() == {}


def test_saving_settings_writes_one_key_by_dotted_path(mongo):
    """Not a read-modify-write of the whole `plugins_settings` map: two admins
    saving different plugins at the same time discarded one of the saves."""
    build("liquidText").set_plugin_settings({"a": 1})

    filters, operator = mongo.operations[0]
    assert filters == {"name": "active_plugins"}
    assert operator == {"$set": {"plugins_settings.liquidText": {"a": 1}}}


def test_another_plugins_settings_are_never_in_the_write(mongo):
    mongo.record = {"plugins_settings": {"filesProcessing": {"types_activation": []}}}

    build("liquidText").set_plugin_settings({"a": 1})

    _, operator = mongo.operations[0]
    assert "filesProcessing" not in json.dumps(operator)


# ---------------------------------------------------------------------------
# Settings validation actually refuses
# ---------------------------------------------------------------------------


def test_a_required_field_is_required(mongo):
    plugin = build("massiveUpdater")

    assert plugin.validate_settings_fields({}, "lunch") is not None
    assert plugin.validate_settings_fields({"file": ["x.xlsx"]}, "lunch") is None


def test_a_bulk_body_must_name_a_content_type(mongo):
    plugin = build("filesProcessing")

    assert plugin.validate_settings_fields({}, "bulk") is not None
    assert plugin.validate_settings_fields({"post_type": ["carpeta"]}, "bulk") is None


def test_an_unknown_settings_group_is_a_404_not_a_500(mongo):
    """The legacy code raised KeyError and returned it as a 500 with the key."""
    payload, status = build("liquidText").settings_payload("nonexistent")

    assert status == 404


# ---------------------------------------------------------------------------
# scheduleSystemTasks
# ---------------------------------------------------------------------------


def _schedule_plugin(monkeypatch, mongo, tasks=("system.index_resources",)):
    plugin = build("scheduleSystemTasks")
    monkeypatch.setattr(
        "archihub.plugins.scheduleSystemTasks.registered_task_names", lambda: list(tasks)
    )
    return plugin


def test_a_schedule_row_must_name_a_task_the_workers_have(monkeypatch, mongo):
    """Otherwise it is scheduled forever and fails every time, recorded only as
    a stream of failed jobs."""
    plugin = _schedule_plugin(monkeypatch, mongo)

    payload, status = plugin.save_settings(
        {"schedule_tasks": [{"task": "not.a.task", "periodicity": "once_a_day", "hour_execution": "03:00"}]}
    )

    assert status == 400
    assert mongo.operations == []


def test_an_unreachable_broker_does_not_block_saving_a_schedule(monkeypatch, mongo):
    """The check is only made when the list could actually be read."""
    plugin = _schedule_plugin(monkeypatch, mongo, tasks=())

    payload, status = plugin.save_settings(
        {"schedule_tasks": [{"task": "anything", "periodicity": "once_a_day", "hour_execution": "03:00"}]}
    )

    assert status == 200


@pytest.mark.parametrize(
    "row",
    [
        {"periodicity": "once_a_day"},
        {"task": "system.index_resources"},
        {"task": "system.index_resources", "periodicity": "once_a_day"},
        {"task": "system.index_resources", "periodicity": "every_x_hours", "interval_value": 0},
        {"task": "system.index_resources", "periodicity": "every_x_hours", "interval_value": "soon"},
    ],
)
def test_an_incomplete_schedule_row_is_refused(monkeypatch, mongo, row):
    plugin = _schedule_plugin(monkeypatch, mongo)

    assert plugin.save_settings({"schedule_tasks": [row]})[1] == 400
    assert mongo.operations == []


def test_an_interval_is_stored_as_a_number(monkeypatch, mongo):
    """`worker/schedule.py` compares it numerically."""
    plugin = _schedule_plugin(monkeypatch, mongo)

    plugin.save_settings(
        {"schedule_tasks": [{"task": "system.index_resources", "periodicity": "every_x_minutes", "interval_value": "30"}]}
    )

    _, operator = mongo.operations[0]
    stored = operator["$set"]["plugins_settings.scheduleSystemTasks"]
    assert stored["schedule_tasks"][0]["interval_value"] == 30


def test_the_task_picker_is_found_by_id_not_by_position(monkeypatch, mongo):
    """The legacy code wrote `resp['settings'][1]['fields']`, so inserting an
    entry above it filled in the wrong one."""
    from archihub.plugins import scheduleSystemTasks

    settings = {"settings": [{"id": "other"}, {"id": "schedule_tasks"}, {"id": "later"}]}

    assert scheduleSystemTasks._find_group(settings, "schedule_tasks") is settings["settings"][1]
    assert scheduleSystemTasks._find_group(settings, "absent") is None


# ---------------------------------------------------------------------------
# filesProcessing: which branch a file takes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mime,path,expected",
    [
        ("audio/wav", "a/b/master.wav", "audio"),
        ("video/mp4", "a/b/master.mp4", "video"),
        ("image/tiff", "a/b/master.tif", "image"),
        ("application/pdf", "a/b/doc.pdf", "pdf"),
        ("text/plain", "a/b/notes.txt", "document"),
        ("application/msword", "a/b/report.doc", "document"),
        ("text/csv", "a/b/data.csv", "csv"),
        # THE BUG: `len(filename.split('.')) != 2` returned None for any name
        # with more than one dot, so this went to the document branch and
        # LibreOffice was asked to convert a CSV.
        ("text/plain", "a/b/interview.final.csv", "csv"),
        ("application/vnd.ms-excel", "a/b/book.xls", "spreadsheet"),
        ("application/octet-stream", "a/b/thing.bin", None),
        (None, "a/b/thing", None),
    ],
)
def test_a_file_is_classified_by_allowlist_not_by_substring(mime, path, expected):
    from archihub.plugins.filesProcessing import classify

    assert classify(mime, path) == expected


def test_hook_order_is_stored_as_a_number(mongo):
    """The legacy save wrote the STRING '0'; the hook bus sorts registrations,
    and sorting a mix of strings and numbers raises TypeError when the hook
    fires - taking the upload with it."""
    plugin = build("filesProcessing")

    plugin.save_settings({"types_activation": [{"type": "carpeta", "order": "3"}]})

    _, operator = mongo.operations[0]
    stored = operator["$set"]["plugins_settings.filesProcessing"]
    assert stored["types_activation"][0]["order"] == 3


# ---------------------------------------------------------------------------
# Cross-plugin capability
# ---------------------------------------------------------------------------


def test_pdf_conversion_is_unavailable_until_its_provider_is_built():
    """The legacy import succeeded whether or not filesProcessing was active,
    so a deactivated plugin's code still ran."""
    with pytest.raises(interop.CapabilityUnavailable) as exc:
        interop.convert_to_pdf("a.docx", "a.pdf")

    assert "filesProcessing" in str(exc.value)


def test_building_filesprocessing_registers_the_capability():
    build("filesProcessing").build()

    assert interop.has(interop.PDF_CONVERSION)


# ---------------------------------------------------------------------------
# Task result downloads
# ---------------------------------------------------------------------------


def test_a_task_result_path_cannot_escape_the_user_files_root(monkeypatch, tmp_path):
    """`USER_FILES_PATH + task['result']` with a result the task chose."""
    from archihub.plugins.framework import base

    monkeypatch.setenv("USER_FILES_PATH", str(tmp_path))
    from archihub.core.settings import get_settings

    get_settings.cache_clear()

    class Mongo:
        def get_record(self, collection, filters=None, fields=None):
            return {
                "taskId": "t",
                "user": "someone",
                "status": "completed",
                "resultType": "file_download",
                "result": "/../../etc/passwd",
            }

    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: Mongo())
    try:
        payload, status = base.task_result_file("t", "someone", is_admin=False)
    finally:
        get_settings.cache_clear()

    assert status == 400


def test_another_users_task_result_is_refused(monkeypatch, tmp_path):
    from archihub.plugins.framework import base

    class Mongo:
        def get_record(self, collection, filters=None, fields=None):
            return {"taskId": "t", "user": "someone-else", "status": "completed"}

    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: Mongo())

    payload, status = base.task_result_file("t", "me", is_admin=False)

    assert status == 401


def test_a_pending_task_has_no_file_yet(monkeypatch):
    from archihub.plugins.framework import base

    class Mongo:
        def get_record(self, collection, filters=None, fields=None):
            return {"taskId": "t", "user": "me", "status": "pending"}

    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: Mongo())

    assert base.task_result_file("t", "me", is_admin=False)[1] == 400


# ---------------------------------------------------------------------------
# Mounting
# ---------------------------------------------------------------------------


def test_a_broken_plugin_does_not_take_the_instance_down(monkeypatch):
    """The legacy web process let a plugin's construction error propagate out of
    create_app, so one plugin with a missing dependency denied access to the
    whole archive. (Its own beat scheduler, meanwhile, caught and skipped.)"""
    from archihub.plugins.framework import mounting

    real_build = mounting.build_plugin

    def explode(slug):
        if slug == "liquidText":
            raise ImportError("no module named 'docx'")
        return real_build(slug)

    monkeypatch.setattr(mounting, "build_plugin", explode)

    app = FastAPI()
    mounted = mounting.mount_plugins(app, ["liquidText", "inventoryMaker"])

    assert set(mounted) == {"inventoryMaker"}
    assert "liquidText" in mounting.get_failed()


def test_an_unported_plugin_is_skipped_rather_than_imported():
    from archihub.plugins.framework import mounting

    app = FastAPI()
    mounted = mounting.mount_plugins(app, ["ocrProcessing"])

    assert mounted == {}


def test_actions_are_tagged_with_the_plugin_that_owns_them():
    """The frontend builds its request URL from this."""
    from archihub.plugins.framework import mounting

    app = FastAPI()
    mounting.mount_plugins(app, ["liquidText"])
    try:
        actions = mounting.system_actions("detail_record")
        assert actions and all(a["plugin"] == "liquidText" for a in actions)
        assert mounting.system_actions("nowhere") == []
    finally:
        mounting.mount_plugins(app, [])


# ---------------------------------------------------------------------------
# Task registration
# ---------------------------------------------------------------------------


def test_every_plugin_task_keeps_its_legacy_dotted_name():
    """These key queued Redis messages and every row in the `tasks` collection."""
    from archihub.plugins import filesProcessing, inventoryMaker, liquidText, massiveUpdater

    assert liquidText.TASK_GENERATE == "liquidText.generateLiquidText"
    assert liquidText.TASK_DOWNLOAD == "liquidText.downloadLiquidText"
    assert filesProcessing.TASK_BULK == "filesProcessing.create_webfile"
    assert filesProcessing.TASK_AUTOMATIC == "filesProcessingCreate.auto"
    assert inventoryMaker.TASK_RESOURCES == "inventoryMaker.create_inventory"
    assert inventoryMaker.TASK_LISTS == "inventoryMaker.create_inventory_lists"
    assert inventoryMaker.TASK_FORMS == "inventoryMaker.create_inventory_forms"
    assert inventoryMaker.TASK_TYPES == "inventoryMaker.create_inventory_types"
    assert massiveUpdater.TASK_UPDATE == "massiveUpdater.update_inventory"


def test_the_plugin_tasks_reach_celerys_registry():
    """A task that is not registered is answered with NotRegistered and dropped
    - which looks like the feature quietly not working."""
    from archihub.worker.celery_app import celery_app

    for slug in PLUGINS:
        __import__(f"archihub.plugins.{slug}")

    registered = set(celery_app.tasks)
    for name in (
        "liquidText.generateLiquidText",
        "filesProcessing.create_webfile",
        "inventoryMaker.create_inventory",
        "massiveUpdater.update_inventory",
    ):
        assert name in registered
