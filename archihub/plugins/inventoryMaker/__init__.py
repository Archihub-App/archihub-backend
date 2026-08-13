"""inventoryMaker — export the catalogue as spreadsheets.

Port of ``app/plugins/inventoryMaker/__init__.py`` and its ``services.py``.

Four exports, all queued: resources (with their files), controlled vocabularies,
metadata standards, and content types. Plus one **unauthenticated** route that
exports a public view's resources synchronously.

THE PUBLIC ROUTE IS THE INTERESTING ONE, and three things about it changed.

**It applies access rights.** The legacy filter was content type plus published
status and nothing else, so a resource under embargo — reserved, visible to
nobody without the right — appeared in an inventory anyone could download
anonymously. It is the same rule as everywhere else in the port: the public
caller's rights are fixed at "none", and only resources requiring none are
exported. BACKEND_FINDINGS S33.

**It stops serving a stale file forever.** The workbook was cached under a name
built from the view and content types, and the generator's first line was
``if not os.path.exists(...)`` — so once written, that name was returned
unchanged for the life of the deployment. A resource catalogued afterwards never
appeared in the public inventory, and nothing said so. The name is now a digest
of what went into it, exactly as the bulk-download archives are, so it changes
when the contents change.

**It cannot be asked for a content type the view does not show.** That check
existed and was right; it is kept, and the request is now also refused when
``post_type`` is not a list or a string, which the original assumed.
"""

from __future__ import annotations

import logging

from celery import shared_task
from fastapi import Body, Depends
from fastapi.responses import JSONResponse

from archihub.core.i18n import gettext as _
from archihub.core.responses import json_response
from archihub.core.security.jwt import CurrentUser
from archihub.plugins.framework.base import (
    ArchiPlugin,
    BrokerUnavailable,
    object_ids,
    queue,
    require_roles,
    task_result_file,
)
from archihub.plugins.inventoryMaker import export

logger = logging.getLogger(__name__)

SLUG = "inventoryMaker"

TASK_RESOURCES = "inventoryMaker.create_inventory"
TASK_LISTS = "inventoryMaker.create_inventory_lists"
TASK_FORMS = "inventoryMaker.create_inventory_forms"
TASK_TYPES = "inventoryMaker.create_inventory_types"

QUEUED_MESSAGE = (
    "The task was added to the processing queue. You can check your profile when it "
    "has finished and download the inventory."
)


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


class InventoryMaker(ArchiPlugin):
    def add_routes(self) -> None:
        plugin = self

        @self.router.post(
            "/public/downloadInventory",
            responses={
                200: {"description": "The inventory spreadsheet"},
                400: {"description": "Malformed request"},
                404: {"description": "Unknown view"},
            },
        )
        def public_inventory(body: dict = Body(...)):
            """Export a public view's resources. **Unauthenticated.**

            Synchronous, because the caller is a browser waiting on a download
            and there is no profile to collect it from later. The result is
            bounded by the view's own visible content types.
            """
            return _public_inventory(body)

        @self.router.post(
            "/bulk",
            status_code=201,
            responses={201: {"description": "Queued"}, 400: {"description": "Invalid selection"}},
        )
        def create_inventory(
            body: dict = Body(...),
            current_user: CurrentUser = Depends(require_roles("admin", "processing", "editor")),
        ) -> JSONResponse:
            """Queue a resource inventory over a selection."""
            error = plugin.validate_settings_fields(body, "bulk")
            if error:
                return json_response({"msg": error}, 400)

            refusal = _may_export(body, current_user.username)
            if refusal:
                return json_response(*refusal)

            return _queue_export(
                resources_task, TASK_RESOURCES, body, current_user.username
            )

        @self.router.post("/bulk-lists", status_code=201)
        def create_inventory_lists(
            body: dict = Body(...),
            current_user: CurrentUser = Depends(require_roles("admin", "editor")),
        ) -> JSONResponse:
            """Queue an export of one controlled vocabulary."""
            if not body.get("parent"):
                return json_response({"msg": _("You must specify a {field}", field="list")}, 400)
            return _queue_export(lists_task, TASK_LISTS, body, current_user.username)

        @self.router.post("/bulk-forms", status_code=201)
        def create_inventory_forms(
            body: dict = Body(...),
            current_user: CurrentUser = Depends(require_roles("admin", "editor")),
        ) -> JSONResponse:
            """Queue an export of one metadata standard."""
            if not body.get("parent"):
                return json_response(
                    {"msg": _("You must specify a {field}", field="form")}, 400
                )
            return _queue_export(forms_task, TASK_FORMS, body, current_user.username)

        @self.router.post("/bulk-types", status_code=201)
        def create_inventory_types(
            body: dict = Body(...),
            current_user: CurrentUser = Depends(require_roles("admin", "editor")),
        ) -> JSONResponse:
            """Queue an export of every content type."""
            return _queue_export(types_task, TASK_TYPES, body, current_user.username)

        @self.router.get(
            "/filedownload/{task_id}",
            responses={200: {"description": "The generated spreadsheet"}},
        )
        def file_download(
            task_id: str,
            current_user: CurrentUser = Depends(require_roles("admin", "processing", "editor")),
        ):
            """Download a completed inventory."""
            from archihub.api.users.services import has_role
            from archihub.core.responses import file_response

            result, status_code = task_result_file(
                task_id, current_user.username, is_admin=has_role(current_user.username, "admin")
            )
            if status_code != 200:
                return json_response(result, status_code)
            return file_response(result, download_name=result.name, as_attachment=True)


def _queue_export(task, task_name: str, body: dict, user: str) -> JSONResponse:
    try:
        queue(task, task_name, user, "file_download", body, user)
    except BrokerUnavailable:
        return json_response({"msg": _("The task queue is unavailable")}, 503)
    return json_response({"msg": _(QUEUED_MESSAGE)}, 201)


def _may_export(body: dict, user: str) -> tuple[dict, int] | None:
    """Whether this caller may inventory these content types and this branch.

    Both checks existed in the original. The second one called
    ``self.has_right(...)``, a method ``PluginClass`` does not define — so it
    raised ``AttributeError`` and the route 500'd for every request that
    supplied a ``parent``. Confirmed latent, and the reason the branch has never
    refused anyone.
    """
    from archihub.api.resources.access import effective_access_right
    from archihub.api.resources.services import can_view_type
    from archihub.api.users.services import has_right, has_role
    from archihub.core.security.jwt import LEGACY_ROLE_FAILURE_STATUS

    is_admin = has_role(user, "admin")

    # `can_view_type` rather than a fourth copy of "read viewRoles, admin
    # bypasses". It is the same rule the catalogue listing and the resource
    # detail route apply, so an export cannot drift into being more permissive
    # than the screen the operator exported from.
    for slug in body.get("post_type") or []:
        if not can_view_type(user, slug):
            return {"msg": _("You do not have sufficient permissions")}, LEGACY_ROLE_FAILURE_STATUS

    parent = body.get("parent")
    parent_id = parent.get("id") if isinstance(parent, dict) else parent
    if parent_id:
        right = effective_access_right(str(parent_id))
        if right and not is_admin and not has_right(user, right):
            return {"msg": _("You do not have sufficient permissions")}, LEGACY_ROLE_FAILURE_STATUS

    return None


# ---------------------------------------------------------------------------
# The public export
# ---------------------------------------------------------------------------


def _public_inventory(body: dict):
    """Generate (or reuse) a public view's inventory and serve it."""
    import hashlib

    from archihub.core import files as filestore
    from archihub.core.responses import file_response
    from archihub.core.settings import get_settings

    view_slug = body.get("view")
    if not isinstance(view_slug, str) or not view_slug:
        return json_response({"msg": _("You must specify a {field}", field="view")}, 400)

    requested = body.get("post_type")
    if requested is None:
        return json_response({"msg": _("No content type was specified")}, 400)

    view = _mongo().get_record("views", {"slug": view_slug})
    if not view:
        return json_response({"msg": _("Unknown view")}, 404)

    visible = view.get("visible") or []

    if requested == "*":
        post_types = list(visible)
    elif isinstance(requested, str):
        post_types = [requested]
    elif isinstance(requested, list) and all(isinstance(p, str) for p in requested):
        post_types = list(requested)
    else:
        # The original assumed a string or a list of strings; a JSON object here
        # became a Mongo operator inside the filter.
        return json_response({"msg": _("No content type was specified")}, 400)

    for post_type in post_types:
        if post_type not in visible:
            # Not a permission failure, despite the message the legacy helper
            # would have produced here. This route is unauthenticated: there is
            # no identity to grant, so an authorisation status tells the caller
            # to go and get a credential that would change nothing. What
            # happened is that the request named a content type this view does
            # not publish - a bad request, like its two neighbours.
            return json_response({"msg": _("The content type is required")}, 400)

    if not post_types:
        return json_response({"msg": _("No content type was specified")}, 400)

    filters: dict = {
        "post_type": {"$in": post_types},
        "status": "published",
        # THE PUBLIC RULE: the caller holds no rights, so only resources that
        # require none are exported. Absent from the legacy filter entirely.
        "$or": [{"accessRights": {"$exists": False}}, {"accessRights": None}, {"accessRights": "public"}],
    }
    if view.get("root") and view.get("parent"):
        filters["parents.id"] = str(view["parent"])

    rows, records = _resource_rows(filters)
    if not rows:
        return json_response({"msg": _("No resources matched the filters")}, 404)

    directory = filestore.resolve_within(get_settings().web_files_path, "inventoryMaker")
    directory.mkdir(parents=True, exist_ok=True)

    # A digest of what went in, so a later catalogue change produces a different
    # name rather than being masked by the cached file.
    digest = hashlib.sha256()
    digest.update(view_slug.encode())
    for post_type in sorted(post_types):
        digest.update(post_type.encode())
    for row in rows[1:]:
        digest.update(str(row.get("id", "")).encode())
    name = f"public_{digest.hexdigest()[:32]}"

    target = directory / f"{name}.xlsx"
    if not target.is_file():
        export.write_workbook(directory, {"Recursos": rows}, name=name)

    return file_response(target, download_name=f"{view_slug}.xlsx", as_attachment=True)


def _resource_rows(filters: dict) -> tuple[list[dict], list[dict]]:
    """Resource rows for a filter, plus the file rows for the same resources."""
    resources = list(_mongo().get_all_records("resources", filters))
    if not resources:
        return [], []

    post_types = sorted({r.get("post_type") for r in resources if r.get("post_type")})
    fields = export.metadata_fields(post_types)
    terms = export.option_term_lookup()

    rows = [export.header_row(fields)]
    resource_ids = []
    for resource in resources:
        rows.append(export.resource_row(resource, fields, terms))
        resource_ids.append(str(resource["_id"]))

    records = list(
        _mongo().get_all_records(
            "records",
            {"parent.id": {"$in": resource_ids}},
            fields={"_id": 1, "name": 1, "displayName": 1, "mime": 1, "size": 1},
        )
    )
    return rows, [export.record_row(record) for record in records]


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@shared_task(ignore_result=False, name=TASK_RESOURCES)
def resources_task(body: dict, user: str) -> str:
    """Export the resources a selection matches, with their files."""
    filters = _selection_filters(body, user)
    rows, records = _resource_rows(filters)
    if not rows:
        raise ValueError("No resources matched the filters")

    directory = export.user_export_directory(user)
    filename = export.write_workbook(directory, {"Recursos": rows, "Archivos": records})
    return f"/{user}/inventoryMaker/{filename}"


@shared_task(ignore_result=False, name=TASK_LISTS)
def lists_task(body: dict, user: str) -> str:
    """Export one controlled vocabulary and its terms."""
    list_ids = object_ids([body.get("parent")], "parent")
    lists = list(_mongo().get_all_records("lists", {"_id": {"$in": list_ids}}))

    rows = [{"id": "id", "name": "name", "description": "description", "options": "options"}]
    terms = export.option_term_lookup()

    for entry in lists:
        option_ids = [str(o) for o in entry.get("options") or []]
        resolved = terms(option_ids)
        rows.append(
            {
                "id": str(entry["_id"]),
                "name": entry.get("name"),
                "description": entry.get("description"),
                "options": ", ".join(sorted(v for v in resolved.values() if v)),
            }
        )

    directory = export.user_export_directory(user)
    filename = export.write_workbook(directory, {"Listado": rows})
    return f"/{user}/inventoryMaker/{filename}"


@shared_task(ignore_result=False, name=TASK_FORMS)
def forms_task(body: dict, user: str) -> str:
    """Export one metadata standard and its fields."""
    forms = list(_mongo().get_all_records("forms", {"slug": str(body.get("parent"))}))

    form_rows = [{"id": "id", "name": "name", "slug": "slug", "description": "description"}]
    field_rows: list[dict] = []

    for form in forms:
        form_rows.append(
            {
                "id": str(form["_id"]),
                "name": form.get("name"),
                "description": form.get("description"),
                "slug": form.get("slug"),
            }
        )
        for field in form.get("fields") or []:
            field_rows.append(
                {
                    "label": field.get("label"),
                    "type": field.get("type"),
                    "destiny": field.get("destiny"),
                    "required": field.get("required"),
                    "instructions": field.get("instructions", ""),
                }
            )

    directory = export.user_export_directory(user)
    filename = export.write_workbook(directory, {"Estandar": form_rows, "Campos": field_rows})
    return f"/{user}/inventoryMaker/{filename}"


@shared_task(ignore_result=False, name=TASK_TYPES)
def types_task(body: dict, user: str) -> str:
    """Export every content type's definition."""
    columns = (
        "id",
        "name",
        "slug",
        "description",
        "metadata",
        "icon",
        "hierarchical",
        "parentType",
        "editRoles",
        "viewRoles",
    )

    rows = [{column: column for column in columns}]
    for post_type in _mongo().get_all_records("post_types"):
        row = {"id": str(post_type.get("_id"))}
        # `.get`, not `[...]`: the original subscripted all nine, so a content
        # type created before one of them existed raised KeyError and the whole
        # export failed.
        row.update({column: post_type.get(column) for column in columns[1:]})
        rows.append(row)

    directory = export.user_export_directory(user)
    filename = export.write_workbook(directory, {"Tipo": rows})
    return f"/{user}/inventoryMaker/{filename}"


def _selection_filters(body: dict, user: str) -> dict:
    """The resource filter for an authenticated export.

    Draft visibility follows the same rule as the rest of the application:
    someone without `publisher` or `admin` sees only their own drafts. The
    original wrote ``if not has_role(user, 'publisher') or not has_role(user,
    'admin')``, which is true unless the caller holds BOTH — so an administrator
    who was not also a publisher was restricted to their own drafts.
    """
    from archihub.api.users.services import has_role

    post_types = body.get("post_type") or []
    filters: dict = {"post_type": {"$in": post_types}}

    parent = body.get("parent")
    parent_id = parent.get("id") if isinstance(parent, dict) else parent

    if parent_id:
        filters = {
            "$or": [
                {"parents.id": str(parent_id), "post_type": {"$in": post_types}},
                {"_id": object_ids([parent_id], "parent")[0]},
            ]
        }

    status = body.get("status", "published")
    clauses = filters["$or"] if "$or" in filters else [filters]

    for clause in clauses:
        clause["status"] = "draft" if status == "draft" else "published"
        if status == "draft" and not (has_role(user, "publisher") or has_role(user, "admin")):
            clause["createdBy"] = user

    return filters


plugin_info = {
    "name": "Exportar inventarios",
    "description": (
        "Plugin para exportar inventarios de los recursos y del contenido del gestor documental."
    ),
    "version": "0.2",
    "author": "Néstor Andrés Peña",
    "type": ["bulk"],
    "settings": {
        "settings_bulk": [
            {
                "type": "instructions",
                "title": "Instrucciones",
                "text": (
                    "Este plugin permite generar inventarios en archivo excel del contenido del "
                    "gestor documental. Para ello, puede especificar el tipo de contenido sobre "
                    "el cual quiere generar el inventario y los filtros que desea aplicar. El "
                    "archivo se encontrará en su perfil para su descarga una vez se haya "
                    "terminado de generar. Es importante notar que el proceso de generación de "
                    "inventarios puede tardar varios minutos, dependiendo de la cantidad de "
                    "recursos que se encuentren en el gestor documental."
                ),
            }
        ],
        "settings_lunch": [],
    },
}


def build() -> InventoryMaker:
    return InventoryMaker(SLUG, plugin_info, module_file=__file__)
