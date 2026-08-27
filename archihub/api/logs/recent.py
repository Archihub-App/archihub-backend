"""Recent activity, filtered by what the caller is entitled to see.

THE ROLE DECIDES THE QUERY, NOT THE PRESENTATION. Every restriction here is a
clause in the database filter, composed with `$and` against anything the caller
asked for. That composition is the whole security property: merged into one
dict, a requested `category` would REPLACE the clause the role imposed, and
asking for the one category a role excludes would return exactly it.

THREE TIERS.

* An administrator sees everything - infrastructure, security, automated
  processing and every user's cataloguing.
* An editor sees the business of the archive: cataloguing and processing. Not
  security, and not infrastructure - so who signed in, whose role changed and
  which service restarted stay out.
* Everyone else sees only what they did themselves, in any category. Their own
  logins included: it is their activity.

AN UNRECOGNISED ACTION IS INFRASTRUCTURE. `category_of` answers `system` for
anything not named below, and the `system` filter is expressed as "none of the
other categories" rather than a list. So an action added by a new plugin, which
nobody has classified yet, is visible to administrators and to nobody else,
without anyone having to remember to add it here.

TITLES ARE RESOLVED THROUGH THE RULE THAT GOVERNS OPENING THE RESOURCE. An entry
naming a reserved resource would otherwise disclose its title to anyone entitled
to see the entry - and this feed is read by editors, not only administrators.
Where the caller may not open it, the entry still appears, with its id and
content type but no title.
"""

from __future__ import annotations

import datetime
import logging

from archihub.core.i18n import gettext as _
from archihub.core.log_actions import log_actions

logger = logging.getLogger(__name__)

COLLECTION = "logs"

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

CATALOGING = "cataloging"
PROCESSING = "processing"
SECURITY = "security"
SYSTEM = "system"

CATEGORIES = (CATALOGING, PROCESSING, SECURITY, SYSTEM)

#: Which category each audited action belongs to. Keyed by the `log_actions`
#: KEY, so the stored value and the lowercase spelling are both derived from one
#: place rather than written twice.
_CATEGORY_BY_KEY = {
    # The business of cataloguing.
    "resource_create": CATALOGING, "resource_update": CATALOGING,
    "resource_article_update": CATALOGING, "resource_granular_update": CATALOGING,
    "resource_delete": CATALOGING, "resource_restore": CATALOGING,
    "resource_open": CATALOGING, "resource_files_order_update": CATALOGING,
    "record_create": CATALOGING, "record_update": CATALOGING,
    "record_delete": CATALOGING, "record_get": CATALOGING, "record_get_all": CATALOGING,
    "type_create": CATALOGING, "type_update": CATALOGING, "type_delete": CATALOGING,
    "form_create": CATALOGING, "form_update": CATALOGING,
    "form_duplicate": CATALOGING, "form_delete": CATALOGING,
    "list_create": CATALOGING, "list_update": CATALOGING, "list_delete": CATALOGING,
    "view_create": CATALOGING, "view_update": CATALOGING, "view_delete": CATALOGING,
    "snap_create": CATALOGING, "snap_delete": CATALOGING,
    "search": CATALOGING,

    # Work handed to a worker: transcription, extraction, analysis.
    "av_transcribe": PROCESSING, "lt_extraction": PROCESSING,
    "img_analyze": PROCESSING, "docseg_extraction": PROCESSING,

    # Who someone is and what they are allowed to do.
    "user_login": SECURITY,

    # Everything else, including "system_update", falls through to SYSTEM.
}


def _spellings(key: str) -> set[str]:
    """Every form one action is stored under.

    An action absent from `log_actions` is written verbatim by
    `normalize_action`, so the lowercase key is a real stored value and not a
    hypothetical one - three such entries exist on this instance.
    """
    return {log_actions.get(key, key), key}


#: Stored values that are NOT infrastructure - what the `system` filter excludes.
_CLASSIFIED = {value for key in _CATEGORY_BY_KEY for value in _spellings(key)}

#: The stored values of each category, both spellings.
BY_CATEGORY = {
    category: {value for key, cat in _CATEGORY_BY_KEY.items()
               if cat == category for value in _spellings(key)}
    for category in (CATALOGING, PROCESSING, SECURITY)
}

#: What an editor may see: the archive's business, never its infrastructure or
#: its security. Stated as a set of categories so the reason is legible.
EDITOR_CATEGORIES = (CATALOGING, PROCESSING)

#: How an entry reads. Absent means `info`.
_LEVELS = {
    "resource_delete": "warning", "record_delete": "warning", "type_delete": "warning",
    "form_delete": "warning", "list_delete": "warning", "view_delete": "warning",
    "snap_delete": "warning",
    "av_transcribe": "success", "lt_extraction": "success",
    "img_analyze": "success", "docseg_extraction": "success",
    "resource_restore": "success",
}


def category_of(action: str | None) -> str:
    """The category one stored action belongs to; `system` when unclassified."""
    if not action:
        return SYSTEM
    for category, values in BY_CATEGORY.items():
        if action in values:
            return category
    return SYSTEM


def level_of(action: str | None) -> str:
    """How an entry reads: `info`, `success` or `warning`.

    There is no `error` level: an audit entry is written once a business action
    has SUCCEEDED, so a failure never reaches this collection. Operational
    failures are in the process log, which is a different thing entirely.
    """
    for key, level in _LEVELS.items():
        if action in _spellings(key):
            return level
    return "info"


# ---------------------------------------------------------------------------
# Who may see what
# ---------------------------------------------------------------------------


def _categories_clause(categories) -> dict:
    """A filter admitting exactly these categories.

    `system` is expressed as the complement of everything classified, so an
    action nobody has categorised is included by it - and therefore reaches
    administrators - rather than falling out of every filter and being visible
    to no one.
    """
    wanted = set(categories)
    if SYSTEM in wanted:
        others = {v for c in wanted if c != SYSTEM for v in BY_CATEGORY[c]}
        return {"action": {"$nin": sorted(_CLASSIFIED - others)}}

    return {"action": {"$in": sorted(v for c in wanted for v in BY_CATEGORY[c])}}


def visibility_clause(username: str, *, is_admin: bool, is_editor: bool) -> dict:
    """The filter deciding what this caller may see at all.

    Composed with `$and` against whatever else was asked for - never merged, so
    a requested category can only ever narrow this.
    """
    if is_admin:
        return {}
    if is_editor:
        return _categories_clause(EDITOR_CATEGORIES)
    return {"username": username}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def parse_since(value) -> datetime.datetime | None:
    """An ISO 8601 instant, in the frame the stored dates use.

    Entries are written with a naive local timestamp, so an offset-aware value
    is converted to local time and its offset dropped. Comparing an aware value
    against naive storage otherwise selects a window hours away from the one
    that was asked for, silently and plausibly.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(_("Invalid date")) from None
    else:
        raise ValueError(_("Invalid date"))

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _limit(value) -> int:
    try:
        requested = int(value) if value not in (None, "") else DEFAULT_LIMIT
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    # Clamped rather than refused: a client asking for more than the ceiling
    # wants as much as it can have, and an error page is a worse answer than a
    # full one.
    return max(1, min(requested, MAX_LIMIT))


def _offset(params) -> int:
    """Where to start, from either `offset` or `page`."""
    try:
        if params.get("offset") not in (None, ""):
            return max(0, int(params["offset"]))
        if params.get("page") not in (None, ""):
            return max(0, int(params["page"])) * _limit(params.get("limit"))
    except (TypeError, ValueError):
        return 0
    return 0


# ---------------------------------------------------------------------------
# Describing an entry
# ---------------------------------------------------------------------------

#: One sentence per action. `%(user)s` is the person, `%(title)s` the resource
#: they acted on. Anything not named here falls back to naming the action, which
#: is still readable and never wrong.
_DESCRIPTIONS = {
    "resource_create": "%(user)s created the resource “%(title)s”",
    "resource_update": "%(user)s updated the resource “%(title)s”",
    "resource_granular_update": "%(user)s updated fields of the resource “%(title)s”",
    "resource_article_update": "%(user)s edited the article of “%(title)s”",
    "resource_delete": "%(user)s moved the resource “%(title)s” to the recycle bin",
    "resource_restore": "%(user)s restored the resource “%(title)s”",
    "resource_open": "%(user)s opened the resource “%(title)s”",
    "resource_files_order_update": "%(user)s reordered the files of “%(title)s”",
    "record_create": "%(user)s added a file to the archive",
    "record_update": "%(user)s updated a stored file",
    "record_delete": "%(user)s deleted a stored file",
    "type_create": "%(user)s created a content type",
    "type_update": "%(user)s updated a content type",
    "type_delete": "%(user)s deleted a content type",
    "form_create": "%(user)s created a form",
    "form_update": "%(user)s updated a form",
    "form_duplicate": "%(user)s duplicated a form",
    "form_delete": "%(user)s deleted a form",
    "list_create": "%(user)s created a list",
    "list_update": "%(user)s updated a list",
    "list_delete": "%(user)s deleted a list",
    "view_create": "%(user)s created a view",
    "view_update": "%(user)s updated a view",
    "view_delete": "%(user)s deleted a view",
    "snap_create": "%(user)s saved a fragment",
    "snap_delete": "%(user)s deleted a fragment",
    "search": "%(user)s searched the archive",
    "user_login": "%(user)s signed in",
    "system_update": "%(user)s changed the system settings",
    "av_transcribe": "%(user)s ran a transcription over %(count)s file(s)",
    "img_analyze": "%(user)s ran an image analysis over %(count)s file(s)",
    "lt_extraction": "%(user)s ran a text extraction over %(count)s file(s)",
    "docseg_extraction": "%(user)s ran a document segmentation over %(count)s file(s)",
}

_TEMPLATE_BY_VALUE = {
    value: template
    for key, template in _DESCRIPTIONS.items()
    for value in _spellings(key)
}


def describe(action: str | None, person: str, resource: dict | None, metadata: dict) -> str:
    """One sentence saying what happened."""
    template = _TEMPLATE_BY_VALUE.get(action or "")
    if not template:
        return _("%(user)s performed the action %(action)s", user=person, action=action or "?")

    if resource is None:
        name = _("(untitled)")
    elif not resource.get("visible", True):
        # WITHHELD IS NOT UNTITLED, and the two must not collapse into one
        # sentence. Calling a reserved resource "(untitled)" states something
        # false about it; calling an untitled one "restricted" states something
        # false about the caller's rights.
        name = _("a restricted resource")
    else:
        name = resource.get("title") or _("(untitled)")

    return _(
        template,
        user=person,
        title=name,
        count=len(metadata.get("ids") or []) or 1,
    )


# ---------------------------------------------------------------------------
# Resolving the people and the resources an entry names
# ---------------------------------------------------------------------------


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _people(usernames: set[str]) -> dict[str, dict]:
    """Resolve every username in one query rather than one per entry."""
    if not usernames:
        return {}
    rows = _mongo().get_all_records(
        "users", {"username": {"$in": sorted(usernames)}},
        fields={"username": 1, "name": 1},
    )
    return {
        row["username"]: {
            "id": str(row["_id"]),
            "username": row["username"],
            "name": row.get("name") or row["username"],
        }
        for row in rows
    }


def _referenced(metadata: dict) -> tuple[str | None, dict | None]:
    """The resource an entry names: its id, and the copy stored with the entry.

    The stored shape varies by action - a whole document for a create or an
    update, a bare id string for a delete - so both are read here rather than at
    each use.
    """
    reference = metadata.get("resource")
    if isinstance(reference, dict):
        identifier = reference.get("_id") or reference.get("id")
        return (str(identifier) if identifier else None), reference
    if isinstance(reference, str) and reference:
        return reference, None
    return None, None


#: Keys of an entry's metadata that hold a copy of the document it acted on.
#: Dropped from the feed: the `resource` field beside them is the same
#: information with the access check applied, and leaving the copy in place
#: hands back the very title that check withheld - as well as several kilobytes
#: of document per entry.
_EMBEDDED_DOCUMENTS = ("resource", "record")


def summarise(details: dict) -> dict:
    """What is left of an entry's metadata once the document copy is removed."""
    return {k: v for k, v in (details or {}).items() if k not in _EMBEDDED_DOCUMENTS}


def _title_of(document: dict | None) -> str | None:
    first_level = ((document or {}).get("metadata") or {}).get("firstLevel") or {}
    title = first_level.get("title")
    return title if isinstance(title, str) and title.strip() else None


def _resources(ids: set[str], username: str, is_admin: bool) -> dict[str, dict]:
    """Resolve the named resources the caller is allowed to have named.

    A resource the caller may not open is still returned - the entry is theirs
    to see - but without its title, which is the part that would disclose it.
    """
    if not ids:
        return {}

    from bson.errors import InvalidId
    from bson.objectid import ObjectId

    from archihub.api.resources.access import may_view_resource

    object_ids = []
    for identifier in ids:
        try:
            object_ids.append(ObjectId(identifier))
        except (InvalidId, TypeError):
            continue
    if not object_ids:
        return {}

    resolved: dict[str, dict] = {}
    rows = _mongo().get_all_records(
        "resources", {"_id": {"$in": object_ids}},
        fields={"post_type": 1, "metadata.firstLevel.title": 1, "accessRights": 1, "parents": 1},
    )
    for row in rows:
        identifier = str(row["_id"])
        visible = is_admin or may_view_resource(username, row, is_admin)
        resolved[identifier] = {
            "id": identifier,
            "post_type": row.get("post_type"),
            "title": _title_of(row) if visible else None,
            # Consumed when the sentence is written and dropped before the
            # entry is returned - a caller reads the absent title, not a flag
            # telling them there was one.
            "visible": visible,
        }
    return resolved


# ---------------------------------------------------------------------------
# The feed
# ---------------------------------------------------------------------------


def recent(params: dict, username: str) -> tuple[dict, int]:
    """Recent activity this caller is entitled to see, newest first.

    The role's clause and the caller's own filters are joined with `$and`. That
    is the security property, not a style choice: merged into a single document
    a requested `category` would overwrite the role's `action` clause, and the
    one category a role is meant to be denied is exactly what asking for it
    would return.
    """
    from archihub.api.logs import services
    from archihub.api.users import services as user_services

    try:
        is_admin = user_services.has_role(username, "admin")
        is_editor = is_admin or user_services.has_role(username, "editor")

        clauses: list[dict] = []

        allowed = visibility_clause(username, is_admin=is_admin, is_editor=is_editor)
        if allowed:
            clauses.append(allowed)

        category = (params.get("category") or "all").strip().lower()
        if category not in (*CATEGORIES, "all"):
            return {"msg": _("Invalid category")}, 400
        if category != "all":
            clauses.append(_categories_clause([category]))

        try:
            since = parse_since(params.get("since"))
        except ValueError as exc:
            return {"msg": str(exc)}, 400
        if since:
            clauses.append({"date": {"$gte": since}})

        filters: dict = {"$and": clauses} if clauses else {}
        limit = _limit(params.get("limit"))
        offset = _offset(params)

        mongo = _mongo()
        rows = list(
            mongo.get_all_records(
                COLLECTION, filters, limit=limit, skip=offset, sort=[("date", -1)]
            )
        )
        total = mongo.count(COLLECTION, filters)

        people = _people({str(row.get("username") or "") for row in rows if row.get("username")})
        referenced = {}
        for row in rows:
            identifier, _stored = _referenced(row.get("metadata") or {})
            if identifier:
                referenced[identifier] = None
        resources = _resources(set(referenced), username, is_admin)

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "data": [_present(row, people, resources, services) for row in rows],
        }, 200
    except Exception as exc:
        logger.exception("Could not read recent activity")
        return {"msg": str(exc)}, 500


def _present(row: dict, people: dict, resources: dict, services) -> dict:
    """One entry, in the shape the activity feed reads."""
    action = row.get("action")
    metadata = row.get("metadata") or {}
    author = str(row.get("username") or "")
    person = people.get(author) or {"id": None, "username": author, "name": author}

    identifier, stored = _referenced(metadata)
    resource = resources.get(identifier) if identifier else None
    if resource is None and stored is not None and identifier is None:
        # Recorded with a copy of the document but no id - nothing to look up,
        # so the entry names what it kept rather than nothing at all.
        resource = {"id": None, "post_type": stored.get("post_type"),
                    "title": _title_of(stored), "visible": True}

    when = row.get("date")
    return {
        "id": str(row.get("_id")) if row.get("_id") else None,
        # A plain ISO string, in the frame the entry was written in - naive and
        # local. A trailing `Z` here would claim UTC for a local timestamp and
        # move every entry by the server's offset.
        "timestamp": when.isoformat() if hasattr(when, "isoformat") else when,
        "action": action,
        "category": category_of(action),
        "level": level_of(action),
        "description": describe(action, person["name"], resource, metadata),
        "user": person,
        "resource": {k: v for k, v in resource.items() if k != "visible"} if resource else None,
        "metadata": summarise(services.build_details(action, metadata)),
    }
