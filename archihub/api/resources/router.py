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

from archihub.api.resources import hierarchy, services
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import LEGACY_ROLE_FAILURE_STATUS, CurrentUser, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resources", tags=["Resources"])

_RESPONSES = {401: {"description": "Missing/invalid token, or insufficient access"}}


def _respond(result) -> JSONResponse:
    payload, status_code = result
    return JSONResponse(status_code=status_code, content=payload)


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
