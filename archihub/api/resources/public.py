"""The public, unauthenticated view of a resource.

Same rule as everywhere else, stated once in ``access.is_public``: published,
unrestricted after inheritance, and a content type declaring no ``viewRoles``.
Nothing here re-derives it.

**A public route answers 404 for "not public"**, byte-identical to "does not
exist". Answering 401 with a distinct message instead confirms to an anonymous
caller that the resource exists and is reserved, which is the fact the refusal
was meant to withhold.

**Content types that restrict viewing are omitted, not refused.** A listing or
a tree that includes a restricted type simply leaves it out - the caller asked
about several types and is entitled to an answer about the public ones. The
original returned 401 for the whole request if *any* requested type had
``viewRoles``, so one restricted type in a saved view blanked the entire public
browse page.
"""

from __future__ import annotations

import logging

from archihub.api.resources import access, article, files, hierarchy, presentation, services
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

COLLECTION = "resources"
PAGE_SIZE = 20

#: Identical for "no such resource" and "not public".
MSG_NOT_FOUND = "Resource does not exist"

#: How much of an article's text a favourite card previews.
FAVOURITE_EXCERPT_CHARS = 300


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def load_public(
    resource_id: str, fields: dict | None = None
) -> tuple[dict | None, tuple[dict, int] | None]:
    """``(resource, error)``. Every public route starts here."""
    object_id = services._to_object_id(resource_id)
    if object_id is None:
        return None, ({"msg": _(MSG_NOT_FOUND)}, 404)

    projection = None
    if fields:
        projection = {**fields, "accessRights": 1, "parents": 1, "status": 1, "post_type": 1}

    resource = _mongo().get_record(COLLECTION, {"_id": object_id}, fields=projection)
    if not resource:
        return None, ({"msg": _(MSG_NOT_FOUND)}, 404)

    if not access.is_public(resource):
        logger.info("Refused anonymous access to resource %s", resource_id)
        return None, ({"msg": _(MSG_NOT_FOUND)}, 404)

    return resource, None


def public_types(slugs) -> list[str]:
    """Those of ``slugs`` whose content type restricts viewing to nobody.

    Omission rather than refusal - see the module docstring.
    """
    return [slug for slug in (slugs or []) if not hierarchy.type_roles(slug).get("viewRoles")]


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def get_all(body: dict) -> tuple[dict, int]:
    """The public browse listing, restricted to published resources.

    Delegates to the authenticated listing with the caller fixed at anonymous:
    the filters, sorting and column handling are identical and were duplicated
    in the original, where the two copies had already drifted.
    """
    requested = body.get("post_type") or []
    if isinstance(requested, str):
        requested = [requested]

    allowed = public_types(requested)
    if not allowed:
        return {"total": 0, "resources": []}, 200

    payload = dict(body)
    payload["post_type"] = allowed
    payload["status"] = "published"

    return services.get_all(payload, user=None)


def get_tree(body: dict) -> tuple[list | dict, int]:
    """The public navigation tree or flat list.

    ``view`` selects between them. The original had no ``else``, so any other
    value - or its absence - fell off the end of the function and returned
    ``None``, which Flask rendered as an empty 500.
    """
    view = body.get("view")
    if view not in ("tree", "list"):
        return {"msg": _("You must specify a view")}, 400

    if view == "tree":
        slugs = [item["slug"] for item in (body.get("tree") or []) if isinstance(item, dict) and item.get("slug")]
    else:
        slugs = body.get("activeTypes") or []

    allowed = public_types(slugs)
    if not allowed:
        return [], 200

    return hierarchy.get_tree(
        body.get("root") or "all",
        allowed,
        None,
        post_type=body.get("postType"),
        page=int(body.get("page") or 0),
        status="published",
    )


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def get_by_id(resource_id: str) -> tuple[dict, int]:
    """One public resource, with its article body hydrated for display."""
    resource, error = load_public(resource_id)
    if error is not None:
        return error

    resource.pop("updatedAt", None)
    resource.pop("updatedBy", None)
    resource = presentation.describe(resource, None, public=True)
    # `_id`, kept as a string - see the note on the authenticated detail route.
    # The same component renders both responses, so a key that exists on one and
    # not the other is a bug on whichever screen reads it.
    resource["_id"] = str(resource["_id"])

    if _is_article(resource.get("post_type")):
        resource["articleBody"] = hydrate_article(resource.get("articleBody") or [])

    return resource, 200


def _is_article(post_type: str | None) -> bool:
    if not post_type:
        return False
    record = _mongo().get_record("post_types", {"slug": post_type}, fields={"isArticle": 1})
    return bool((record or {}).get("isArticle"))


# ---------------------------------------------------------------------------
# Article hydration
# ---------------------------------------------------------------------------


def hydrate_article(article_body: list) -> list:
    """Expand the blocks that reference other things into displayable content.

    Three block kinds embed ids rather than content: uploaded records, snaps and
    favourites. Each is resolved here so the published page can render without
    the reader making further authenticated calls.

    **Every embedded thing is checked for public visibility on its own.** A
    block referencing something reserved yields an empty block rather than
    leaking it - an article is published by one person and may cite material
    another restricted afterwards.
    """
    blocks = [block for block in article_body if isinstance(block, dict)]

    for block in blocks:
        kind = block.get("type")
        try:
            if kind == "uploadedRecords":
                block["content"] = _hydrate_records(block.get("content"))
            elif kind == "snap":
                block["content"] = _hydrate_snaps(block.get("content"))
            elif kind == "favorite":
                block["content"] = _hydrate_favorite(block.get("content"))
        except Exception:
            # One malformed block must not take down the whole page. The
            # original let it raise, so a single bad reference 500'd the
            # article.
            logger.exception("Could not hydrate a %s block", kind)
            block["content"] = []

    return blocks


def _hydrate_records(content) -> list:
    from archihub.api.records import public as record_public

    ids = article.extract_ids(content, "data-records")
    hydrated = []
    for record_id in ids:
        record, error = record_public.load_public(record_id)
        if error is not None:
            continue
        kind = ((record.get("processing") or {}).get("fileProcessing") or {}).get("type")
        hydrated.append(
            {
                "id": str(record["_id"]),
                "name": record.get("displayName") or record.get("name"),
                "type": kind,
            }
        )
    return hydrated


def _hydrate_snaps(content) -> list:
    from archihub.api.records import public as record_public

    ids = article.extract_ids(content, "data-snaps")
    if not ids:
        return []

    object_ids = [oid for oid in (services._to_object_id(i) for i in ids) if oid is not None]
    snaps = _mongo().get_all_records(
        "snaps",
        {"_id": {"$in": object_ids}},
        fields={"data": 1, "type": 1, "record_id": 1},
    )

    hydrated = []
    for snap in snaps:
        # A snap is only as public as the record it points at.
        _record, error = record_public.load_public(snap.get("record_id") or "")
        if error is not None:
            continue
        hydrated.append(
            {
                "id": str(snap["_id"]),
                "recordId": str(snap.get("record_id")) if snap.get("record_id") else None,
                "data": snap.get("data"),
                "type": snap.get("type"),
            }
        )
    return hydrated


def _hydrate_favorite(content):
    favourite_id, source = article.extract_favorite(content)
    if not favourite_id or source not in ("resources", "records"):
        return []

    if source == "resources":
        resource, error = load_public(favourite_id)
        if error is not None:
            return []
        return {
            "id": str(resource["_id"]),
            "source": source,
            "data": {
                "name": _title(resource),
                "files": len(resource.get("filesObj") or []) or None,
                "articleBody": _excerpt(resource.get("articleBody")),
                "thumbnail": _thumbnail(resource),
                "type": None,
            },
        }

    from archihub.api.records import public as record_public

    record, error = record_public.load_public(favourite_id)
    if error is not None:
        return []

    kind = ((record.get("processing") or {}).get("fileProcessing") or {}).get("type")
    return {
        "id": str(record["_id"]),
        "source": source,
        "data": {
            "name": record.get("displayName") or record.get("name"),
            "files": None,
            "articleBody": None,
            "thumbnail": None,
            "type": kind,
        },
    }


def _title(resource: dict) -> str | None:
    metadata = resource.get("metadata")
    if isinstance(metadata, dict):
        first = metadata.get("firstLevel")
        if isinstance(first, dict):
            return first.get("title")
    return None


def _excerpt(article_body) -> str:
    """The first few hundred characters of an article's prose, tags removed."""
    if not isinstance(article_body, list):
        return ""

    text = ""
    for block in article_body:
        if not isinstance(block, dict) or block.get("type") != "paragraph":
            continue
        cleaned = article.strip_html(block.get("content"))
        if not cleaned:
            continue
        text = cleaned if not text else f"{text} {cleaned}"
        if len(text) > FAVOURITE_EXCERPT_CHARS:
            return text[:FAVOURITE_EXCERPT_CHARS]

    return text


def _thumbnail(resource: dict) -> str | None:
    """A small inline preview of a favourited resource's first image.

    Returned as a data URI because the card renders before any authenticated
    request could be made. Only the medium derivative, and only when the record
    behind it is itself public.
    """
    import base64

    from archihub.api.records import public as record_public
    from archihub.core import files as filestore
    from archihub.core.settings import get_settings

    entries = files.ordered_file_entries(resource)
    for entry in entries:
        record, error = record_public.load_public(entry["id"])
        if error is not None:
            continue

        file_processing = (record.get("processing") or {}).get("fileProcessing") or {}
        if file_processing.get("type") != "image" or not file_processing.get("path"):
            continue

        try:
            path = filestore.resolve_within(
                get_settings().web_files_path, file_processing["path"] + "_medium.jpg"
            )
        except filestore.UnsupportedFile:
            continue

        if not path.is_file():
            continue

        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
        return f"data:image/jpeg;base64,{encoded}"

    return None


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def get_files(resource_id: str, page: int, group_images: bool) -> tuple[dict, int]:
    resource, error = load_public(resource_id, {"filesObj": 1})
    if error is not None:
        return error
    return files.list_files(resource, None, page, group_images, public=True)


def get_images(resource_id: str) -> tuple[dict, int]:
    resource, error = load_public(resource_id, {"filesObj": 1})
    if error is not None:
        return error
    return files.count_images(resource)


def download(resource_id: str, kind: str):
    resource, error = load_public(resource_id, {"filesObj": 1})
    if error is not None:
        return error
    return files.bulk_download(resource, kind, None, public=True)
