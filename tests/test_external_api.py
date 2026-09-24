"""`/adminApi` and `/publicApi` — the surfaces other organisations script against.

The first section pins that a lookup never uses the request body as a Mongo
filter. It needs an admin API token, but an API token is a long-lived credential
handed to an integration, so "the caller is trusted" is not a defence.
"""

from __future__ import annotations

import pytest
from fastapi import Request

from archihub.api.external import services


class FakeMongo:
    def __init__(self):
        self.resources: list[dict] = []
        self.options: list[dict] = []
        self.queries: list[dict] = []

    def get_record(self, collection, filters=None, fields=None):
        self.queries.append(filters or {})
        rows = self.options if collection == "options" else self.resources
        for row in rows:
            if all(row.get(k) == v for k, v in (filters or {}).items()):
                return dict(row)
        return None

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        return []

    def count(self, collection, filters=None):
        return 0


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# The request body is not a query
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"$where": "sleep(5000)"},
        {"ident": {"$ne": None}},
        {"post_type": {"$regex": "(a+)+$"}},
        {"metadata.firstLevel.title": {"$exists": True}},
        {"$or": [{"a": 1}, {"b": 2}]},
    ],
)
def test_an_operator_never_reaches_the_query(mongo, body):
    """The body never becomes a Mongo filter."""
    payload, status = services.find_resource(body)

    assert status == 400
    assert mongo.queries == []


def test_an_unknown_field_is_dropped_rather_than_queried(mongo):
    with pytest.raises(services.InvalidRequest):
        services.build_lookup({"createdBy": "someone", "password": "x"})


def test_a_legitimate_lookup_still_works(mongo):
    filters = services.build_lookup({"ident": "AH-001", "post_type": "fondo"})

    assert filters == {"ident": "AH-001", "post_type": "fondo", "status": "published"}


def test_a_first_level_metadata_lookup_is_allowed(mongo):
    filters = services.build_lookup({"metadata.firstLevel.title": "A folder"})

    assert filters["metadata.firstLevel.title"] == "A folder"


def test_the_status_is_fixed_and_not_client_settable(mongo):
    """This endpoint answers about published material only."""
    filters = services.build_lookup({"ident": "x", "status": "draft"})

    assert filters["status"] == "published"


def test_a_lookup_with_nothing_usable_in_it_is_refused(mongo):
    with pytest.raises(services.InvalidRequest):
        services.build_lookup({})


def test_a_body_that_is_not_an_object_is_refused(mongo):
    for body in ("everything", ["a"], 42, None):
        with pytest.raises(services.InvalidRequest):
            services.build_lookup(body)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_a_found_resource_is_returned_in_the_contract_shape(mongo):
    from bson.objectid import ObjectId

    mongo.resources.append(
        {
            "_id": ObjectId("6a70b833497d4440325c94b1"),
            "ident": "AH-001",
            "status": "published",
            "post_type": "fondo",
            "metadata": {"firstLevel": {"title": "A fonds"}},
            "filesObj": [{"id": "x"}],
            "parent": [], "parents": [],
        }
    )

    payload, status = services.find_resource({"ident": "AH-001"})

    assert status == 200
    assert set(payload) == {"id", "post_type", "metadata", "filesObj", "parent", "parents"}
    assert payload["id"] == "6a70b833497d4440325c94b1"


def test_a_resource_missing_optional_fields_does_not_500(mongo):
    """A resource missing optional fields is still a valid answer."""
    from bson.objectid import ObjectId

    mongo.resources.append(
        {"_id": ObjectId("6a70b833497d4440325c94b1"), "ident": "AH-002", "status": "published"}
    )

    payload, status = services.find_resource({"ident": "AH-002"})

    assert status == 200
    assert payload["metadata"] == {} and payload["filesObj"] == []


def test_a_missing_resource_is_a_404(mongo):
    payload, status = services.find_resource({"ident": "nope"})

    assert status == 404


def test_an_option_lookup_needs_a_term(mongo):
    """An absent `term` is a 400."""
    payload, status = services.find_option({})

    assert status == 400


@pytest.mark.parametrize("term", [{"$ne": None}, 42, [""], ""])
def test_an_option_term_that_is_not_text_is_refused(mongo, term):
    payload, status = services.find_option({"term": term})

    assert status == 400
    assert mongo.queries == []


def test_a_found_option_returns_its_id(mongo):
    from bson.objectid import ObjectId

    mongo.options.append({"_id": ObjectId("6a70b833497d4440325c94c1"), "term": "Photograph"})

    payload, status = services.find_option({"term": "Photograph"})

    assert (payload, status) == ({"id": "6a70b833497d4440325c94c1"}, 200)


# ---------------------------------------------------------------------------
# Defaults an integration may omit
# ---------------------------------------------------------------------------


def test_the_fields_an_integration_may_omit_are_filled_in(monkeypatch):
    monkeypatch.setattr(
        "archihub.api.system.services.get_setting_value",
        lambda name, entry, fallback=None: "carpeta",
    )

    filled = services.with_defaults({"metadata": {}})

    assert filled["post_type"] == "carpeta"
    assert filled["status"] == "published"
    assert filled["parent"] == [] and filled["parents"] == []
    assert filled["filesIds"] == []


def test_an_explicit_value_is_not_overwritten(monkeypatch):
    monkeypatch.setattr(
        "archihub.api.system.services.get_setting_value",
        lambda name, entry, fallback=None: "carpeta",
    )

    filled = services.with_defaults({"post_type": "serie", "status": "draft"})

    assert filled["post_type"] == "serie"
    assert filled["status"] == "draft"


def test_update_cache_is_accepted_and_dropped(monkeypatch):
    """The cache is invalidated by every write, so there is nothing to ask for."""
    monkeypatch.setattr(
        "archihub.api.system.services.get_setting_value",
        lambda name, entry, fallback=None: "carpeta",
    )

    assert "updateCache" not in services.with_defaults({"updateCache": True})


def test_an_update_gets_its_deleted_files_list(monkeypatch):
    monkeypatch.setattr(
        "archihub.api.system.services.get_setting_value",
        lambda name, entry, fallback=None: "carpeta",
    )

    assert services.with_defaults({}, update=True)["deletedFiles"] == []
    assert "deletedFiles" not in services.with_defaults({})


def test_no_default_content_type_configured_is_a_clear_refusal(monkeypatch):
    monkeypatch.setattr(
        "archihub.api.system.services.get_setting_value",
        lambda name, entry, fallback=None: None,
    )

    with pytest.raises(services.InvalidRequest):
        services.with_defaults({})


# ---------------------------------------------------------------------------
# Availability and the plugin proxy
# ---------------------------------------------------------------------------


def test_a_switched_off_api_is_indistinguishable_from_a_missing_route(monkeypatch):
    """The handler refuses the way an unrouted path is refused."""
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from archihub.api.external import router

    monkeypatch.setattr(router, "_enabled", lambda entry: False)

    with pytest.raises(StarletteHTTPException) as refused:
        router.get_system_info(identity=None)

    assert refused.value.status_code == 404


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/adminApi/get_system_info"),
        ("POST", "/adminApi/get_id"),
        ("GET", "/publicApi/types"),
        ("POST", "/publicApi"),
    ],
)
def test_a_switched_off_api_answers_404_before_it_looks_at_the_token(
    monkeypatch, method, path
):
    """Through the real app, with NO credential.

    The test above calls the handler function directly and so never resolves
    the dependencies. Here they run: an API that is switched off must answer
    404 before any identity dependency could answer 401, so it gives away
    nothing about the route.
    """
    from fastapi.testclient import TestClient

    from archihub.api.external import router
    from main import app

    monkeypatch.setattr(router, "_enabled", lambda entry: False)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.request(method, path, json={})

    assert response.status_code == 404
    # And no hint that authentication was even considered.
    assert "token" not in response.text.lower()

    # Byte-identical to a path that genuinely does not exist. A *translated*
    # "not found" here would still give the prober their answer.
    assert response.json() == client.get("/no-such-route-at-all").json()


def test_a_switched_off_api_is_404_even_with_a_well_formed_token(monkeypatch):
    """Otherwise a valid credential still distinguishes off from missing."""
    from fastapi.testclient import TestClient

    from archihub.api.external import router
    from main import app

    monkeypatch.setattr(router, "_enabled", lambda entry: False)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/adminApi/get_system_info", headers={"Authorization": "Bearer x"})

    assert response.status_code == 404


def test_the_plugin_proxy_refuses_rather_than_reaching_a_route_that_is_not_there(monkeypatch):
    """The target is looked up, never assembled from the request."""
    from archihub.api.external import router

    monkeypatch.setattr(router, "_enabled", lambda entry: True)

    class Identity:
        username = "root"

    from starlette.exceptions import HTTPException as StarletteHTTPException

    # 404: no plugin named "x" is mounted, so there is nothing to resolve
    # against - which is the answer whatever the endpoint string contained.
    with pytest.raises(StarletteHTTPException) as refused:
        router.plugin_proxy(
            request=None, plugin="x", plugin_endpoint="../../users/delete", identity=Identity()
        )
    assert refused.value.status_code == 404


@pytest.mark.parametrize(
    "endpoint",
    [
        "../../users/delete",
        "..%2f..%2fusers",
        "bulk/../../../system/clear-cache",
        "settings/../../../../etc/passwd",
        "filedownload/../bulk",
        "filedownload/abc/../../../users",
        "filedownload/a/b",
        "filedownload/..",
        "filedownload/%2e%2e",
        "filedownload/.",
        "",
    ],
)
def test_a_traversal_string_resolves_to_nothing(monkeypatch, endpoint):
    """Resolution is against the plugin's OWN route list, so a traversal
    string simply matches none of them - there is nothing to filter because the
    only reachable values come from a table the application built."""
    from archihub.api.external import router
    from archihub.plugins.framework.mounting import _mounted
    from archihub.plugins.liquidText import build

    plugin = build()
    plugin.build()
    _mounted["liquidText"] = plugin
    try:
        assert router.resolve_plugin_route("liquidText", endpoint) is None
    finally:
        _mounted.clear()


def test_a_real_plugin_endpoint_resolves():
    """The counterpart: resolution has to actually work, or the 404 above would
    be indistinguishable from the feature being broken."""
    from archihub.api.external import router
    from archihub.plugins.framework.mounting import _mounted
    from archihub.plugins.liquidText import build

    plugin = build()
    plugin.build()
    _mounted["liquidText"] = plugin
    try:
        assert router.resolve_plugin_route("liquidText", "bulk") == "/liquidText/bulk"
        assert router.resolve_plugin_route("liquidText", "/bulk/") == "/liquidText/bulk"
        # A route with a parameter takes one concrete value in that segment.
        assert (
            router.resolve_plugin_route("liquidText", "filedownload/6ab3dd714de35380d592d678")
            == "/liquidText/filedownload/6ab3dd714de35380d592d678"
        )
    finally:
        _mounted.clear()


# ---------------------------------------------------------------------------
# The plugin proxy, end to end
# ---------------------------------------------------------------------------


@pytest.fixture
def proxied(monkeypatch):
    """An app with the admin API and a fake plugin whose routes authenticate
    the way every plugin route does - a session, through get_current_user."""
    from fastapi import APIRouter, Depends, FastAPI
    from fastapi.responses import Response
    from fastapi.testclient import TestClient

    from archihub.api.external import router
    from archihub.core.security.api_auth import ApiIdentity
    from archihub.core.security.jwt import CurrentUser, get_current_user
    from archihub.plugins.framework.mounting import _mounted

    plugin_router = APIRouter(prefix="/fake")

    @plugin_router.api_route("/echo/{item}", methods=["GET", "POST"])
    async def echo(item: str, request: Request, user: CurrentUser = Depends(get_current_user)):
        return {
            "user": user.username,
            "item": item,
            "method": request.method,
            "query": dict(request.query_params),
            "body": (await request.body()).decode(),
            "content_type": request.headers.get("content-type", ""),
            "cookie": request.headers.get("cookie"),
        }

    @plugin_router.get("/file")
    def file(user: CurrentUser = Depends(get_current_user)):
        return Response(
            content=b"%PDF-1.4 fake",
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="report.pdf"'},
        )

    class Plugin:
        pass

    fake = Plugin()
    fake.router = plugin_router
    _mounted["fake"] = fake

    app = FastAPI()
    app.include_router(router.admin_router)
    app.include_router(plugin_router)
    app.dependency_overrides[router.admin_identity] = lambda: ApiIdentity(
        username="root", is_admin=True
    )
    monkeypatch.setattr(router, "_enabled", lambda entry: True)
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        _mounted.clear()


def test_the_proxy_reaches_the_plugin_route_as_the_token_owner(proxied):
    response = proxied.get(
        "/adminApi/plugins/fake/echo/abc?page=2&q=x",
        headers={"Authorization": "Bearer an-admin-api-token", "Cookie": "session=browser"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user"] == "root"
    assert body["item"] == "abc"
    assert body["query"] == {"page": "2", "q": "x"}
    # The browser cookie does not ride along on a call made with an API token.
    assert body["cookie"] is None


def test_the_proxy_forwards_a_json_body(proxied):
    response = proxied.post("/adminApi/plugins/fake/echo/abc", json={"records": ["1", "2"]})

    assert response.status_code == 200
    assert response.json()["method"] == "POST"
    assert response.json()["body"] == '{"records":["1","2"]}'


def test_the_proxy_forwards_a_multipart_upload(proxied):
    response = proxied.post(
        "/adminApi/plugins/fake/echo/abc",
        data={"data": '{"a": 1}'},
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["content_type"].startswith("multipart/form-data; boundary=")
    assert 'name="data"' in body["body"] and "hello" in body["body"]


def test_the_proxy_passes_a_file_download_through(proxied):
    response = proxied.get("/adminApi/plugins/fake/file")

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 fake"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="report.pdf"'


def test_an_undeclared_endpoint_is_a_plain_404(proxied):
    response = proxied.get("/adminApi/plugins/fake/nothing-here")

    assert response.status_code == 404


def test_a_method_the_route_does_not_take_is_refused_by_the_route(proxied):
    response = proxied.delete("/adminApi/plugins/fake/echo/abc")

    assert response.status_code == 405


def test_the_routes_keep_their_published_paths():
    """External integrations are addressed by these strings and nothing in this
    repository would catch a change to them.

    THE SET IS TRANSCRIBED FROM THE STACK BEING REPLACED, not from what this
    module happens to declare. Pinning what is here would only assert that
    nobody changed it - it would agree with a missing route just as readily as
    with a complete one, which is how `/adminApi/lists/{id}` went unnoticed.
    """
    from archihub.api.external.router import admin_router, public_router

    admin = {route.path for route in admin_router.routes}
    public = {route.path for route in public_router.routes}

    assert admin == {
        "/adminApi/get_system_info",
        "/adminApi/create",
        "/adminApi/update",
        "/adminApi/get_id",
        "/adminApi/get_opts_id",
        "/adminApi/create_type",
        "/adminApi/update_type",
        "/adminApi/get_type/{slug}",
        "/adminApi/lists/{list_id}",
        "/adminApi/plugins/{plugin}/{plugin_endpoint:path}",
    }
    assert public == {"/publicApi", "/publicApi/types", "/publicApi/resources/{resource_id}"}


def test_an_unknown_plugin_is_answered_exactly_like_a_missing_route(monkeypatch):
    """Through the real app, with a valid admin identity: a plugin or endpoint
    this instance does not run gets the same bytes as a path that does not
    exist, not a translated variant of them."""
    import time

    from fastapi.testclient import TestClient

    from archihub.api.external import router
    from archihub.core import i18n
    from archihub.core.security.api_auth import ApiIdentity
    from main import app

    monkeypatch.setattr(router, "_enabled", lambda entry: True)
    # Spanish, where a translated "Not Found" reads differently from Starlette's.
    monkeypatch.setattr(i18n, "_locale_cache", ("es", time.monotonic()))
    app.dependency_overrides[router.admin_identity] = lambda: ApiIdentity(
        username="root", is_admin=True
    )
    try:
        client = TestClient(app, raise_server_exceptions=False)
        unknown = client.get("/adminApi/plugins/no-such-plugin/anything")
        missing = client.get("/no-such-route-at-all")
    finally:
        app.dependency_overrides.pop(router.admin_identity, None)

    assert unknown.status_code == missing.status_code == 404
    assert unknown.content == missing.content
