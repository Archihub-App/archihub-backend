"""Which extra roles pass each permission point.

SCOPE: the three administrator routes over ``archihub.core.permissions``: the
catalogue of points, the roles configured on them, and changing those roles.
Services return ``(payload, status)`` tuples.

An update only ever names points from the catalogue and custom roles from the
roles vocabulary; a built-in role is refused. Both are checked before anything
is written, and a refusal writes nothing, so a saved document never holds a
role or point that does not exist.
"""

from __future__ import annotations

import logging

from archihub.core.i18n import gettext as _
from archihub.core.permissions import MAPPINGS_SETTING, POINTS, get_mappings

logger = logging.getLogger(__name__)

COLLECTION = "system"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _register_log(user: str, metadata: dict) -> None:
    from archihub.api.logs.services import register_log

    register_log(user, "role_mappings_update", metadata)


def list_points() -> tuple[dict, int]:
    """Every permission point, with its built-in roles and translated description."""
    points = [
        {
            "id": point.id,
            "domain": point.domain,
            "description": _(point.description),
            "defaultRoles": list(point.default_roles),
            "builtinRule": _(point.builtin_rule) if point.builtin_rule else None,
        }
        for point in POINTS.values()
    ]
    return {"points": points}, 200


def get_role_mappings() -> tuple[dict, int]:
    """The configured roles of every point; a point with none has an empty list."""
    try:
        stored = get_mappings()
    except Exception:
        logger.exception("Could not read the role mappings")
        return {"msg": _("Error while processing the request")}, 500
    return {"mappings": {point_id: stored.get(point_id, []) for point_id in POINTS}}, 200


def _validate(mappings) -> tuple[dict[str, list[str]] | None, str | None]:
    """``(cleaned, None)`` or ``(None, message)`` for the body's ``mappings``."""
    from archihub.core.roles import BUILTIN_ROLES, verify_roles_exist

    if not isinstance(mappings, dict) or not mappings:
        return None, _('"{field}" must be a non-empty object', field="mappings")

    cleaned: dict[str, list[str]] = {}
    for point_id, roles in mappings.items():
        if point_id not in POINTS:
            return None, _("The permission point {point} does not exist", point=point_id)
        if not isinstance(roles, list) or not all(isinstance(r, str) and r for r in roles):
            return None, _("The roles of {point} must be a list of role ids", point=point_id)
        builtin = next((role for role in roles if role in BUILTIN_ROLES), None)
        if builtin is not None:
            return None, _(
                "{role} is a built-in role and cannot be added to a permission point",
                role=builtin,
            )
        try:
            verified = verify_roles_exist(roles)
        except ValueError as exc:
            return None, str(exc)
        cleaned[point_id] = list(dict.fromkeys(verified))
    return cleaned, None


def update_role_mappings(body: dict, user: str) -> tuple[dict, int]:
    """Set the configured roles of the points named in ``body["mappings"]``.

    Points not named keep what they have. An empty list removes a point's
    configured roles, leaving only its built-in ones.
    """
    cleaned, error = _validate((body or {}).get("mappings"))
    if error is not None:
        return {"msg": error}, 400

    try:
        current = get_mappings()
        changed = {
            point_id: roles
            for point_id, roles in cleaned.items()
            if roles != current.get(point_id, [])
        }
        merged = {**current, **cleaned}
        merged = {point_id: roles for point_id, roles in merged.items() if roles}

        _mongo().upsert_record(
            COLLECTION, {"name": MAPPINGS_SETTING}, {"name": MAPPINGS_SETTING, "data": merged}
        )
    except Exception:
        logger.exception("Could not save the role mappings")
        return {"msg": _("Error while processing the request")}, 500

    if changed:
        logger.info("%s changed the roles of %s", user, ", ".join(sorted(changed)))
        _register_log(user, {"mappings": changed})
    return {"msg": _("Role mappings updated"), "mappings": {
        point_id: merged.get(point_id, []) for point_id in POINTS
    }}, 200
