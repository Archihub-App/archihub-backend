"""Resource routes.

Port of ``app/api/resources/routes.py``, in slices. This one covers the READ
path: the catalogue listing and single-resource detail.

Not yet ported: create/update/delete/restore, the article editor, file ordering,
the tree, the record sub-resources, and the public mirror of all of it. Those
carry the write path and the 11 hook call sites.

Role failures keep the legacy 401 pending the coordinated frontend flip.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

import json

from fastapi import File, Form, UploadFile

from archihub.api.records.storage import IncomingFile, UnsupportedFileType
from archihub.api.resources import article, editing, hierarchy, services, write
from archihub.core.files import UnsupportedFile, UploadTooLarge
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import (
    LEGACY_ROLE_FAILURE_STATUS,
    CurrentUser,
    get_current_user,
    require_role_any,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resources", tags=["Resources"])

_RESPONSES = {401: {"description": "Missing/invalid token, or insufficient access"}}


def _respond(result) -> JSONResponse:
    payload, status_code = result
    return JSONResponse(status_code=status_code, content=payload)


# The editorial routes are gated twice: coarsely here, so a reader never reaches
# the service at all, and precisely inside it against the specific resource.
require_editor = require_role_any(
    "admin", "editor", "super_editor", status_code=LEGACY_ROLE_FAILURE_STATUS
)


@router.post(
    "/getall",
    responses={200: {"description": "Paginated resources"}, **_RESPONSES},
)
def get_all(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """The catalogue listing.

    A POST because the filter, the requested columns and the sort all travel in
    the body - the legacy shape, which the frontend sends.

    Results are constrained by the caller's access rights unless they are an
    administrator; see ``resources/access.py``.
    """
    return _respond(services.get_all(body, current_user.username))


def _parse_data(data: str) -> dict:
    """The JSON document the frontend sends inside a multipart ``data`` field.

    Kept as hand-parsed JSON rather than a Pydantic model: the metadata inside
    it is validated against the content type's runtime-defined form, which no
    static schema can express. See PLAN_FASTAPI.md section 7.
    """
    try:
        parsed = json.loads(data)
    except (TypeError, ValueError):
        raise ValueError(_("The data field is not valid JSON"))

    if not isinstance(parsed, dict):
        raise ValueError(_("The data field must be an object"))
    return parsed


def _incoming(uploads: list[UploadFile] | None, body: dict) -> list[IncomingFile]:
    """Pair each upload with the file field it belongs to.

    ``filesIds`` carries one entry per upload, in order, naming the form's file
    field (``filetag``) and the position the file should take.
    """
    tags = body.get("filesIds") or []
    incoming = []

    for index, upload in enumerate(uploads or []):
        tag = tags[index] if index < len(tags) else {}
        if not isinstance(tag, dict):
            tag = {}
        incoming.append(
            IncomingFile.from_upload(
                upload, tag=tag.get("filetag") or "file", order=tag.get("order")
            )
        )

    return incoming


def _write_response(call) -> JSONResponse:
    """Run a write and turn its file-level refusals into ordinary answers."""
    try:
        return _respond(call())
    except UploadTooLarge as exc:
        return JSONResponse(status_code=413, content={"msg": str(exc)})
    except (UnsupportedFileType, UnsupportedFile) as exc:
        return JSONResponse(status_code=400, content={"msg": str(exc)})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"msg": str(exc)})


@router.post(
    "",
    responses={
        201: {"description": "Resource created"},
        400: {"description": "Validation failed, or an unusable file"},
        413: {"description": "A file exceeds the upload ceiling"},
        **_RESPONSES,
    },
)
def create(
    data: str = Form(..., description="JSON document describing the resource"),
    files: list[UploadFile] = File(default_factory=list),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Create a resource, with its files.

    Multipart, because it carries uploads: the resource itself travels as a
    JSON string in ``data``.

    Files are stored only once the metadata validates — otherwise a rejected
    form would leave orphaned bytes on disk every time someone saved an
    incomplete one.
    """
    try:
        body = _parse_data(data)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"msg": str(exc)})

    return _write_response(
        lambda: write.create(body, current_user.username, _incoming(files, body))
    )


@router.delete(
    "",
    responses={200: {"description": "Resources moved to the recycle bin"}, **_RESPONSES},
)
def delete_by_id(
    body: list = Body(...),
    current_user: CurrentUser = Depends(require_editor),
) -> JSONResponse:
    """Move resources, and everything filed below them, to the recycle bin.

    Nothing is destroyed. Permission is checked for **every** id before
    anything is written — the legacy version deleted its way down the list and
    stopped at the first refusal, leaving the batch half-applied.
    """
    return _respond(write.delete(body, current_user.username))


@router.post(
    "/restore",
    responses={200: {"description": "Resources restored"}, **_RESPONSES},
)
def restore_by_id(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_editor),
) -> JSONResponse:
    """Bring resources back out of the recycle bin.

    Restored as **drafts**, never straight to published: something withdrawn
    from the catalogue should not silently reappear in the public one.
    """
    return _respond(
        write.restore(
            body.get("ids"), current_user.username, bool(body.get("recursive", False))
        )
    )


@router.post(
    "/tree",
    responses={
        200: {"description": "One level of the navigation tree"},
        400: {"description": "Unrecognised view, or a missing required field"},
        **_RESPONSES,
    },
)
def get_tree(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """One level of the archive's navigation tree.

    Two modes, selected by ``view``:

    * ``tree`` - hierarchical navigation. ``tree`` carries the content types the
      client wants to walk; each is dropped if the caller lacks its view roles.
    * ``list`` - a flat, paginated level, optionally scoped to one content type
      (``postType``) instead of the client's ``activeTypes``.

    An unrecognised ``view`` now returns 400. The legacy route fell off the end
    of its own ``if``/``elif`` and returned ``None``, which Flask could not
    serialise - so a typo in this field produced a 500 with no explanation.
    """
    view = body.get("view")
    root = body.get("root")
    if not root:
        return JSONResponse(status_code=400, content={"msg": _("A root is required")})

    if view == "tree":
        requested = [item.get("slug") for item in (body.get("tree") or []) if item.get("slug")]
        slugs = hierarchy.visible_type_slugs(current_user.username, requested)
        return _respond(hierarchy.get_tree(root, slugs, current_user.username))

    if view == "list":
        status = body.get("status") or "published"
        post_type = body.get("postType") or None

        if status == "draft":
            from archihub.api.users.services import has_role

            if not has_role(current_user.username, "editor") and not has_role(
                current_user.username, "admin"
            ):
                return JSONResponse(
                    status_code=LEGACY_ROLE_FAILURE_STATUS,
                    content={"msg": _("You don't have the required authorization")},
                )

        if post_type:
            requested = _slugs_for_post_type(post_type)
            if requested is None:
                return JSONResponse(status_code=404, content={"msg": _("Post type not found")})
        else:
            requested = [s for s in (body.get("activeTypes") or []) if s]

        slugs = hierarchy.visible_type_slugs(current_user.username, requested)
        page = body.get("page")
        return _respond(
            hierarchy.get_tree(
                root,
                slugs,
                current_user.username,
                post_type=post_type,
                page=int(page) if page is not None else 0,
                status=status,
            )
        )

    return JSONResponse(status_code=400, content={"msg": _("Unknown view")})


def _slugs_for_post_type(post_type: str) -> list[str] | None:
    """A content type plus every type above it, which is the level's scope.

    ``None`` means the type does not exist - reported as 404 rather than the
    legacy 500 it produced by subscripting an error tuple.
    """
    from archihub.api.types.services import get_by_slug

    resolved = get_by_slug(post_type)
    if isinstance(resolved, tuple) or not isinstance(resolved, dict):
        return None

    slugs = [resolved.get("slug")]
    for parent in resolved.get("parentsTypes") or []:
        slug = parent.get("slug")
        if slug and slug not in slugs:
            slugs.append(slug)
    return [s for s in slugs if s]


@router.get(
    "/favcount/{resource_id}",
    # The identity is not used, only required - so the dependency is declared on
    # the route rather than taken as an unread parameter.
    dependencies=[Depends(get_current_user)],
    responses={
        200: {"description": "How many users have favourited this resource"},
        404: {"description": "No such resource"},
        **_RESPONSES,
    },
)
def favcount(resource_id: str) -> JSONResponse:
    """The favourite count of a resource.

    Declared before ``/{resource_id}`` so the literal segment wins the match.
    """
    return _respond(services.get_fav_count(resource_id))


@router.post(
    "/updateorder/{resource_id}",
    responses={
        200: {"description": "File order updated"},
        404: {"description": "No such resource"},
        **_RESPONSES,
    },
)
def update_file_order(
    resource_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_editor),
) -> JSONResponse:
    """Move files to new positions within a resource.

    Send only the files that moved, as ``{"files": [{"id": ..., "order": ...}]}``
    - the rest keep their relative order and everything is renumbered from zero.
    """
    return _respond(editing.update_files_order(resource_id, body, current_user.username))


@router.post(
    "/change-post-type",
    responses={
        200: {"description": "Permission verified"},
        404: {"description": "No such resource"},
        **_RESPONSES,
    },
)
def change_post_type(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_editor),
) -> JSONResponse:
    """Verify the caller may edit a resource's current content type.

    DESPITE THE NAME, NOTHING IS CHANGED. This is a permission check that was
    never finished; the legacy Swagger already documents it as such and the
    response message is preserved because the frontend displays it. See
    BACKEND_FINDINGS F25.
    """
    return _respond(editing.change_post_type(body, current_user.username))


@router.put(
    "/{record_id}/granular",
    responses={
        200: {"description": "Field updated on the record's parent resources"},
        400: {"description": "Unusable path or value, or no parent could be updated"},
        404: {"description": "No such record, or it has no parent resources"},
        **_RESPONSES,
    },
)
def update_granular_by_id(
    record_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_editor),
) -> JSONResponse:
    """Set one free-text field across every resource a file belongs to.

    ``{record_id}`` is a **record** (a file), not a resource, despite the path -
    the transcription and OCR tools work file-by-file and write their result up
    into the catalogue entries that file belongs to.

    Partial success is reported as success: a caller entitled to edit only some
    of the parents gets those updated and a count.
    """
    return _respond(
        editing.update_granular(
            record_id,
            body.get("metadataPath"),
            body.get("value", ""),
            current_user.username,
            concat=bool(body.get("concat", False)),
        )
    )


@router.get(
    "/{resource_id}/article",
    responses={
        200: {"description": "The article body, or null if the resource has none"},
        404: {"description": "No such resource"},
        **_RESPONSES,
    },
)
def get_article_body(
    resource_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """The long-form article attached to a resource.

    Readable by anyone who may read the resource itself, which includes access
    rights inherited from its ancestors.
    """
    return _respond(article.get_article_body(resource_id, current_user.username))


@router.post(
    "/{resource_id}/article",
    responses={
        200: {"description": "Article body updated"},
        400: {"description": "Missing or malformed article body"},
        404: {"description": "No such resource"},
        **_RESPONSES,
    },
)
def update_article_body(
    resource_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Replace a resource's article.

    Only ``articleBody`` is written. The legacy route built its update from the
    entire request body, so other resource fields could be changed through it.
    """
    return _respond(article.update_article_body(resource_id, body, current_user.username))


@router.post(
    "/{resource_id}/article/comments",
    responses={
        200: {"description": "Comment added"},
        400: {"description": "Missing comment, or an unusable block reference"},
        404: {"description": "No such resource, or no such block"},
        **_RESPONSES,
    },
)
def add_article_block_comment(
    resource_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Attach a reviewer comment to one block of the article.

    Address the block by ``blockId`` where possible - ``blockIndex`` is only
    meaningful against the version of the article the client was looking at.
    """
    return _respond(article.add_block_comment(resource_id, body, current_user.username))


@router.put(
    "/{resource_id}",
    responses={
        200: {"description": "Resource updated"},
        400: {"description": "Validation failed, or an unusable file"},
        413: {"description": "A file exceeds the upload ceiling"},
        **_RESPONSES,
    },
)
def update_by_id(
    resource_id: str,
    data: str = Form(..., description="JSON document describing the resource"),
    files: list[UploadFile] = File(default_factory=list),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Replace a resource's metadata, parents and file set.

    ``deletedFiles`` removes attachments and ``updatedFiles`` repositions them;
    both travel inside ``data``. Moving the resource in the tree rewrites the
    stored ancestry of everything below it.
    """
    try:
        body = _parse_data(data)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"msg": str(exc)})

    return _write_response(
        lambda: write.update(resource_id, body, current_user.username, _incoming(files, body))
    )


@router.get(
    "/{resource_id}",
    responses={
        200: {"description": "The resource"},
        404: {"description": "Not found, or not visible to this caller"},
        **_RESPONSES,
    },
)
def get_by_id(
    resource_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """One resource by id.

    A resource the caller may not see returns 404 rather than 403 - a distinct
    status would confirm that it exists.
    """
    return _respond(services.get_by_id(resource_id, current_user.username))
