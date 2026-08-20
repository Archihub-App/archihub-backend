"""Running a search, for a caller who may or may not exist.

One implementation with the caller's rights as a parameter, rather than an
authenticated copy and a public copy that drift apart. Two services calling one
builder with ``user`` and ``None`` look equivalent, and the difference that
matters — that a public caller must not choose a publication state — belongs to
neither of them.
"""

from __future__ import annotations

import logging

from archihub.api.search import query as query_builder
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import ROLE_FAILURE_STATUS

logger = logging.getLogger(__name__)

RESOURCES_INDEX = "resources"

#: What the indexer writes for a resource that declares no access right, and so
#: what an anonymous caller is entitled to.
PUBLIC_RIGHT = "public"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _client():
    from archihub.infra.search import get_search

    return get_search()


def indexing_enabled() -> bool:
    """Whether this instance has search switched on.

    Read at request time rather than at import: an operator turning indexing on
    should not need a restart to make the routes work, and the legacy design —
    where the whole blueprint was registered or not at construction — meant they
    did.
    """
    from archihub.api.system.services import get_setting_value

    return bool(get_setting_value("index_management", "index_activation"))


# ---------------------------------------------------------------------------
# What the caller may see
# ---------------------------------------------------------------------------


def visible_types(post_types: list[str], user: str | None) -> tuple[list[str], str | None]:
    """``(types, error)``. Content types this caller may search.

    A type restricted by ``viewRoles`` is **refused**, not silently dropped:
    unlike a browse listing, a search asks a direct question and answering it
    with fewer types than were asked for looks like "no results" rather than
    "not for you". The legacy version raised a bare exception here, which its
    own caller turned into a 500 — documented in its Swagger as such.
    """
    from archihub.api.resources.hierarchy import type_roles
    from archihub.api.users.services import has_role

    for post_type in post_types:
        roles = type_roles(post_type).get("viewRoles") or []
        if not roles:
            continue
        if user is None:
            return [], _("You don't have the required authorization")
        if has_role(user, "admin") or any(has_role(user, role) for role in roles):
            continue
        return [], _("You don't have the required authorization")

    return post_types, None


def access_rights_for(user: str | None) -> list[str]:
    """The access-right values a caller's results may carry."""
    if user is None:
        return [PUBLIC_RIGHT]

    from archihub.api.resources.access import user_access_rights

    return [*user_access_rights(user), PUBLIC_RIGHT]


def _declared_fields(post_types: list[str]) -> tuple[set[str], set[str]]:
    """``(every declared field, those that are text)`` across the requested types.

    Drives both the `_source` allowlist and which sort fields need `.keyword`.
    """
    from archihub.api.types.services import get_metadata

    declared: set[str] = set()
    text: set[str] = set()

    for post_type in post_types:
        try:
            form = get_metadata(post_type)
        except Exception:
            logger.warning("Could not resolve the form for %s", post_type, exc_info=True)
            continue
        for field in (form or {}).get("fields") or []:
            destiny = field.get("destiny")
            if not destiny:
                continue
            declared.add(destiny)
            if field.get("type") in ("text", "text-area"):
                text.add(destiny)

    return declared, text


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------


def search(body: dict, user: str | None, *, public: bool) -> tuple[dict, int]:
    """Run a search as this caller.

    ``public=True`` fixes the publication state at *published* and the rights at
    *public*, and nothing in the request can change either.
    """
    from archihub.infra.search import SearchUnavailable

    if not indexing_enabled():
        return {"msg": _("Search is not enabled on this instance")}, 503

    if not isinstance(body, dict):
        return {"msg": _("The request body must be an object")}, 400

    try:
        post_types = query_builder._strings(body.get("post_type"), "post_type")
        if not post_types:
            raise query_builder.InvalidSearch(_("You must specify a post type"))

        allowed, error = visible_types(post_types, user)
        if error is not None:
            # A refusal, not a failure: the caller is known and may not see
            # these types. 403 rather than a server error.
            return {"msg": error}, ROLE_FAILURE_STATUS

        declared, sortable_text = _declared_fields(allowed)
        statuses = query_builder.resolve_status(
            body.get("status"),
            public=public,
            may_see_drafts=_may_see_drafts(user),
            may_see_deleted=_may_see_deleted(user),
        )

        built = query_builder.build(
            body,
            statuses=statuses,
            access_rights=access_rights_for(user),
            declared_fields=declared,
            sortable_text_fields=sortable_text,
        )
    except query_builder.InvalidSearch as exc:
        return {"msg": str(exc)}, 400

    client = _client()
    try:
        raw = client.search(RESOURCES_INDEX, built)
    except SearchUnavailable as exc:
        logger.warning("Search failed: %s", exc)
        # The cluster's own words are logged, not returned - they name indices
        # and field mappings.
        return {"msg": _("The search could not be completed")}, 502

    response = shape(raw, body, view=body.get("viewType") or "list")
    _audit(user, body)
    return response, 200


def _may_see_drafts(user: str | None) -> bool:
    if user is None:
        return False
    from archihub.api.users.services import has_role

    return has_role(user, "admin") or has_role(user, "publisher") or has_role(user, "editor")


def _may_see_deleted(user: str | None) -> bool:
    if user is None:
        return False
    from archihub.api.users.services import has_role

    return has_role(user, "admin")


# ---------------------------------------------------------------------------
# Shaping the answer
# ---------------------------------------------------------------------------


def shape(raw: dict, body: dict, *, view: str) -> dict:
    """Turn an Elasticsearch response into what the interface renders."""
    hits = raw.get("hits") or {}
    total = (hits.get("total") or {}).get("value", 0)

    rights = _right_terms()
    resources = []

    for hit in hits.get("hits") or []:
        source = hit.get("_source") or {}
        resource = {**source, "id": hit.get("_id")}

        held = resource.get("accessRights")
        if held == PUBLIC_RIGHT:
            resource.pop("accessRights", None)
        elif held:
            term = rights.get(held)
            if term is None:
                # An access right the instance no longer defines. Dropping the
                # document is the conservative reading: we cannot say who it is
                # for, so we do not show it.
                logger.info("Dropping a result carrying an unknown access right")
                continue
            resource["accessRights"] = term

        highlight = (hit.get("highlight") or {}).get("text")
        if highlight:
            resource["text"] = highlight[0]

        resources.append(resource)

    if view == "blog" and not body.get("full_article"):
        for resource in resources:
            article = resource.get("article") or ""
            if len(article) > query_builder.ARTICLE_EXCERPT:
                resource["article"] = article[: query_builder.ARTICLE_EXCERPT] + "..."

    return {"total": total, "resources": resources}


def _right_terms() -> dict[str, str]:
    from archihub.core.roles import get_access_rights

    try:
        options = get_access_rights().get("options") or []
    except Exception:
        logger.warning("Could not resolve access-right terms", exc_info=True)
        return {}
    return {option.get("id"): option.get("term") for option in options if option.get("id")}


def _audit(user: str | None, body: dict) -> None:
    from archihub.api.logs.services import register_log

    # Only the shape of the search is recorded, not every filter verbatim: the
    # keyword is what a reader typed and the log is readable by administrators.
    register_log(
        user or "public",
        "search",
        {
            "post_type": body.get("post_type"),
            "viewType": body.get("viewType"),
            "has_keyword": bool(body.get("keyword")),
        },
    )
