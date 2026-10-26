"""Permission points: the role checks an administrator may extend.

A permission point is one place where the application requires a role. Each
point has the built-in roles the code has always required, and an
administrator may add roles of their own to it. The roles that pass a point are
the union of the two: configuring a point can widen who passes it, never narrow
it, so the built-in roles keep working whatever is stored. Only custom roles
can be added; a built-in role (``archihub.core.roles.BUILTIN_ROLES``) means the
same thing everywhere, so it is never given a point it does not already pass.

THE CATALOGUE IS CLOSED. :data:`POINTS` is the only source of point ids, and an
update naming any other id is refused. That is how the actions that must stay
with administrators are kept out: they have no point, so there is nothing to
map a role to. They are:

* everything in the users domain: accounts, their roles, their API keys;
* global system configuration: settings, plugin activation and settings, the
  roles vocabulary, restarts, the cache, indexing, storage cleanup, the AI
  providers and their credentials;
* the audit log and the task administration screens;
* the records filter search, whose safety rests on its administrator gate;
* the administrator bypass itself. An ``is_admin`` flag means "every rule is
  waived", and making it configurable would be handing out administration.

Storage: one document in the ``system`` collection,
``{"name": "role_mappings", "data": {point id: [role ids]}}``. Writes to that
collection invalidate the cache, so a saved mapping takes effect on the next
request in every process.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from archihub.infra.cache import cached

logger = logging.getLogger(__name__)

#: The ``name`` of the ``system`` document holding the configured roles.
MAPPINGS_SETTING = "role_mappings"


@dataclass(frozen=True)
class Point:
    """One configurable role check.

    ``description`` is an untranslated message id, translated when listed.
    ``builtin_rule`` is set where the built-in requirement is not "any of
    ``default_roles``"; it describes the rule for an operator, and the call site
    implements it.
    """

    id: str
    domain: str
    description: str
    default_roles: tuple[str, ...]
    builtin_rule: str | None = None


_CATALOGUE = (
    # Content modelling
    Point("types.create", "types", "Create content types", ("admin",)),
    Point(
        "types.manage", "types",
        "Read, edit and delete content types, including their edit and view roles",
        ("admin", "editor"),
    ),
    Point("forms.manage", "forms", "Create, edit, duplicate and delete metadata forms", ("admin",)),
    Point("lists.manage", "lists", "Create, edit and delete controlled vocabularies", ("admin", "editor")),
    Point("views.manage", "views", "Create, edit and delete the saved views the explore screens are built from", ("admin", "editor")),
    Point(
        "access_rights.read", "system",
        "Read the access-rights vocabulary, as the cataloguing forms do",
        ("admin", "editor"),
    ),
    # Resources
    Point(
        "resources.edit", "resources",
        "Delete, restore, reorder, re-type and partially edit resources. The content "
        "type's edit roles and the ownership rule still apply",
        ("admin", "editor", "super_editor"),
    ),
    Point(
        "resources.super_edit", "resources",
        "Edit and delete resources created by other users",
        ("admin", "super_editor"),
    ),
    Point("resources.publish", "resources", "Publish resources", ("admin", "publisher")),
    Point(
        "resources.see_deleted", "resources",
        "See and search the recycle bin",
        ("admin",),
    ),
    Point(
        "resources.see_all_drafts", "resources",
        "See other users' drafts in the catalogue listing",
        (),
        builtin_rule="Built in: users holding both publisher and admin",
    ),
    Point(
        "resources.browse_drafts", "resources",
        "Browse drafts in the flat listing of the navigation tree",
        ("admin", "editor"),
    ),
    # Records
    Point(
        "records.edit_blocks", "records",
        "Edit the block layout of records they can open",
        ("admin", "editor"),
    ),
    Point(
        "records.transcribe", "records",
        "Correct transcriptions of records with an open task assigned to them",
        ("admin", "editor", "transcriber"),
    ),
    Point(
        "records.transcribe_any", "records",
        "Correct any transcription, without an assigned task. Also needs "
        "records.transcribe",
        ("admin", "team_lead", "editor"),
        builtin_rule="Built in: admin and team_lead; editor too, unless they also hold "
        "transcriber",
    ),
    # User tasks
    Point(
        "usertasks.manage", "usertasks",
        "Create, assign, list and approve user tasks",
        ("admin", "team_lead"),
    ),
    Point(
        "usertasks.comment", "usertasks",
        "Comment on user tasks",
        ("admin", "team_lead", "editor"),
    ),
    # Activity feed
    Point(
        "activity.catalogue", "activity",
        "See other users' cataloguing and processing activity in the activity feed",
        ("admin", "editor"),
    ),
    # AI services
    Point(
        "ai.use", "aiservices",
        "Use the language models: providers and skills listings, chat, the record assistant",
        ("admin", "processing", "llm"),
    ),
    Point(
        "ai.manage_skills", "aiservices",
        "Create, edit, delete and synchronise AI skills",
        ("admin", "processing"),
    ),
)

#: Every permission point, by id.
POINTS: dict[str, Point] = {point.id: point for point in _CATALOGUE}


def get_point(point_id: str) -> Point:
    """The point named ``point_id``. Raises ``KeyError`` for an unknown id."""
    return POINTS[point_id]


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _clean(data) -> dict[str, list[str]]:
    """The stored mappings, reduced to known points and custom role ids."""
    from archihub.core.roles import BUILTIN_ROLES

    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, list[str]] = {}
    for point_id, roles in data.items():
        if point_id not in POINTS or not isinstance(roles, list):
            continue
        cleaned[point_id] = [
            role for role in roles
            if isinstance(role, str) and role and role not in BUILTIN_ROLES
        ]
    return cleaned


@cached("system")
def get_mappings() -> dict[str, list[str]]:
    """The configured roles of every point that has any.

    An unreadable or malformed document means no configured roles: the built-in
    roles still apply, so a failure here can only narrow access back to them.
    """
    try:
        record = _mongo().get_record("system", {"name": MAPPINGS_SETTING})
    except Exception:
        logger.exception("Could not read the role mappings; only built-in roles apply")
        return {}
    return _clean((record or {}).get("data") if isinstance(record, dict) else None)


def configured_roles(point_id: str) -> list[str]:
    """The roles an administrator has added to ``point_id``."""
    get_point(point_id)
    return list(get_mappings().get(point_id, []))


def allowed_roles(point_id: str) -> list[str]:
    """The built-in roles of ``point_id`` followed by its configured ones."""
    point = get_point(point_id)
    roles = list(point.default_roles)
    roles.extend(role for role in configured_roles(point_id) if role not in roles)
    return roles
