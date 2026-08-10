"""Turning a search request into an Elasticsearch query.

**THE CALLER DOES NOT CHOOSE WHAT MAY BE SEARCHED.** The publication state and
the access-rights clause are decided here from *who is asking*, and a request
cannot influence either. That is the whole point of this module, and it is what
the legacy builder got wrong:

    {'term': {'status.keyword': body['status'] if 'status' in body else 'published'}}

with the public service passing the caller's body through untouched. Since the
indexer indexes every resource whatever its state and defaults `accessRights` to
``public``, an anonymous request asking for ``status: "draft"`` read unpublished
material — **demonstrated against a real index, 16 drafts.** Recorded as
BACKEND_FINDINGS S28.

Two other rules follow from the same thought — a search endpoint is reachable by
people who are not signed in, so everything they send is adversarial until
proven otherwise:

* **The keyword is a *simple* query string, not a query string.** `query_string`
  is Elasticsearch's full DSL in a string: it accepts regular expressions and
  leading wildcards, which is a denial of service on an open endpoint, and it
  raises on malformed syntax, so a stray bracket typed into a search box was a
  500. `simple_query_string` never throws and its features are selectable.
* **`_source` is an allowlist.** The legacy builder appended the request's
  `activeColumns` to `_source` verbatim, letting the caller pick which stored
  fields came back — including fields the content type marks as restricted,
  which the resource detail route is careful to withhold.
"""

from __future__ import annotations

import logging

from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

#: Fields every result carries, regardless of what was asked for.
BASE_SOURCE = ("post_type", "accessRights", "ident", "files", "createdAt", "status")

#: Publication states that exist. A public caller gets exactly one of them.
PUBLIC_STATUS = "published"
DRAFT_STATES = ("draft", "created", "updated")
ALL_STATES = (PUBLIC_STATUS, *DRAFT_STATES, "deleted")

#: Media kinds a result may be filtered to.
RECORD_TYPES = ("image", "document", "video", "audio")

#: Views the frontend renders, each adding its own fields and filters.
VIEW_TYPES = ("list", "gallery", "blog")

#: Ceiling on one page of results. Elasticsearch's own `max_result_window`
#: defaults to 10,000 documents for `from + size`, and asking beyond it is an
#: error rather than an empty page — so both are bounded here, before the query
#: is built, rather than surfacing as a cluster error.
MAX_SIZE = 100
DEFAULT_SIZE = 20
MAX_WINDOW = 10000

#: `simple_query_string` features that are safe on an open endpoint.
#: Deliberately excludes `PREFIX` (leading wildcards) and never enables regex,
#: which `simple_query_string` does not support at all — that is why it is used.
QUERY_FLAGS = "AND|OR|NOT|PHRASE|PRECEDENCE|WHITESPACE|ESCAPE"

#: How much of a blog article is returned when the caller has not asked for all
#: of it.
ARTICLE_EXCERPT = 300


class InvalidSearch(Exception):
    """The request does not describe a search that can be run."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _strings(value, field: str) -> list[str]:
    """A list of plain strings, or a clear refusal.

    Every one of these becomes a term in a query. A nested object here is not a
    filter the caller is entitled to write.
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidSearch(_('"{field}" must be a list of text values', field=field))
    return [item for item in value if item]


def _bounded_int(value, *, default: int, minimum: int, maximum: int, field: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InvalidSearch(_('"{field}" must be a number', field=field))
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise InvalidSearch(_('"{field}" must be a number', field=field)) from None
    if number < minimum:
        raise InvalidSearch(_('"{field}" must be a number', field=field))
    return min(number, maximum)


def resolve_status(requested, *, public: bool, may_see_drafts: bool, may_see_deleted: bool) -> list[str]:
    """Which publication states this caller may search.

    A public caller gets ``published`` and nothing else, whatever they asked
    for — not an error, because a request naming a state they cannot see is
    answered with what they can, and telling them otherwise confirms the state
    exists.
    """
    if public:
        return [PUBLIC_STATUS]

    if requested is None:
        return [PUBLIC_STATUS]
    if not isinstance(requested, str) or requested not in ALL_STATES:
        raise InvalidSearch(_('"{field}" must be a text value', field="status"))

    if requested == "deleted":
        if not may_see_deleted:
            raise InvalidSearch(_("You don't have the required authorization"))
        return ["deleted"]

    if requested in DRAFT_STATES:
        if not may_see_drafts:
            raise InvalidSearch(_("You don't have the required authorization"))
        # "draft" covers the three pre-publication states, exactly as the
        # resources listing treats it.
        return list(DRAFT_STATES)

    return [PUBLIC_STATUS]


def resolve_source(active_columns, declared_fields: set[str]) -> list[str]:
    """Which stored fields a result may carry.

    Restricted to what the requested content types actually declare, so a caller
    cannot name an arbitrary indexed field and have it returned. The legacy
    builder appended `activeColumns` to `_source` unchecked.
    """
    requested = []
    for column in active_columns or []:
        destiny = column.get("destiny") if isinstance(column, dict) else column
        if isinstance(destiny, str) and destiny and destiny in declared_fields:
            requested.append(destiny)

    return list(dict.fromkeys([*BASE_SOURCE, *requested]))


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def build(
    body: dict,
    *,
    statuses: list[str],
    access_rights: list[str],
    declared_fields: set[str],
    sortable_text_fields: set[str],
) -> dict:
    """The Elasticsearch query for one search request."""
    post_types = _strings(body.get("post_type"), "post_type")
    if not post_types:
        raise InvalidSearch(_("You must specify a post type"))

    size = _bounded_int(body.get("size"), default=DEFAULT_SIZE, minimum=0, maximum=MAX_SIZE, field="size")
    page = _bounded_int(body.get("page"), default=0, minimum=0, maximum=MAX_WINDOW, field="page")
    offset = page * size
    if offset + size > MAX_WINDOW:
        raise InvalidSearch(
            _("That page is beyond the searchable range; narrow the search instead")
        )

    view = body.get("viewType") or "list"
    if view not in VIEW_TYPES:
        raise InvalidSearch(_('"{field}" must be a text value', field="viewType"))

    source = resolve_source(body.get("activeColumns"), declared_fields)

    filters: list[dict] = [
        {"terms": {"post_type.keyword": post_types}},
        {"terms": {"status.keyword": statuses}},
        {"terms": {"accessRights.keyword": access_rights}},
    ]

    query: dict = {
        "track_total_hits": True,
        "query": {"bool": {"filter": filters}},
        "size": size,
        "from": offset,
        "_source": source,
    }

    keyword = body.get("keyword")
    if isinstance(keyword, str) and keyword.strip():
        operator = "and" if (body.get("operator") or "AND").upper() == "AND" else "or"
        matcher: dict = {
            "query": keyword,
            "default_operator": operator,
            "flags": QUERY_FLAGS,
            # A term that matches nothing is a search with no results, not a
            # failed request.
            "lenient": True,
        }
        fields = _strings(body.get("input_filters"), "input_filters")
        if fields:
            matcher["fields"] = fields
        query["query"]["bool"]["must"] = [{"simple_query_string": matcher}]

    _apply_sort(query, body, sortable_text_fields)
    _apply_filters(query, body)
    _apply_view(query, body, view, size, offset)

    return query


def _apply_sort(query: dict, body: dict, sortable_text_fields: set[str]) -> None:
    sort_by = body.get("sortBy") or "createdAt"
    if not isinstance(sort_by, str):
        raise InvalidSearch(_('"{field}" must be a text value', field="sortBy"))
    if sort_by.lower() == "relevance":
        return

    # A text field is not sortable in Elasticsearch; its `.keyword` subfield is.
    field = f"{sort_by}.keyword" if sort_by in sortable_text_fields else sort_by
    direction = "asc" if (body.get("sortOrder") or "asc") == "asc" else "desc"
    query["sort"] = [{field: {"order": direction}}]


def _apply_filters(query: dict, body: dict) -> None:
    filters = query["query"]["bool"]["filter"]

    if body.get("files"):
        filters.append({"range": {"files": {"gte": 1}}})

    record_types = [t for t in _strings(body.get("record_types") or body.get("record_type"), "record_types") if t in RECORD_TYPES]
    if record_types:
        filters.append({"terms": {"records.type.keyword": record_types}})

    parents = body.get("parents")
    if isinstance(parents, dict) and isinstance(parents.get("id"), str):
        filters.append({"term": {"parents.id": parents["id"]}})

    for entry in body.get("date_filters") or []:
        if not isinstance(entry, dict):
            continue
        destiny, span = entry.get("destiny"), entry.get("range")
        if not isinstance(destiny, str) or not isinstance(span, list) or len(span) != 2:
            continue
        filters.append({"range": {destiny: {"gte": span[0], "lte": span[1]}}})


def _apply_view(query: dict, body: dict, view: str, size: int, offset: int) -> None:
    """Each view adds the fields it renders and the filter that makes it meaningful."""
    if view == "gallery":
        query["_source"] = [*query["_source"], "records"]
        query["query"]["bool"]["filter"].append({"term": {"records.type.keyword": "image"}})

    elif view == "blog":
        query["_source"] = [*query["_source"], "article", "records"]
        query["query"]["bool"]["filter"].append(
            {
                "bool": {
                    "must": [{"exists": {"field": "article"}}],
                    "must_not": [{"term": {"article": ""}}],
                }
            }
        )
        query["sort"] = [{"createdAt": {"order": "desc"}}]
