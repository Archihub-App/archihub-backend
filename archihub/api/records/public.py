"""The public, unauthenticated view of a record.

Same files, no caller. Everything here answers one question first - *is this
record public?* - and that question has exactly one implementation,
``access.is_public``, which composes with the resource rule rather than
restating it. Applying a slightly different subset of the rule per route is how
a file attached to an unpublished draft ends up served anonymously.

**A public route must never say more than "no".** Where the authenticated API
distinguishes "no such record" from "not yours to read", this returns 404 for
both. Telling an anonymous caller that a record exists but is reserved confirms
the id, and ids are not secret - they appear in the authenticated listing every
cataloguer can see.
"""

from __future__ import annotations

import logging

from archihub.api.records import access, media, services, transcription, viewers
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

COLLECTION = "records"

#: Deliberately identical for "does not exist" and "is not public".
MSG_NOT_FOUND = "Record does not exist"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def load_public(record_id: str) -> tuple[dict | None, tuple[dict, int] | None]:
    """``(record, error)``. The public counterpart of ``services.load_visible``.

    Every public route starts here, for the same reason the authenticated ones
    start at ``load_visible``: the rule is applied once, in one place.
    """
    object_id = services._to_object_id(record_id)
    if object_id is None:
        return None, ({"msg": _(MSG_NOT_FOUND)}, 404)

    record = _mongo().get_record(
        COLLECTION, {"_id": object_id}, fields=services.DETAIL_FIELDS
    )
    if not record:
        return None, ({"msg": _(MSG_NOT_FOUND)}, 404)

    if not access.is_public(record):
        logger.info("Refused anonymous access to record %s", record_id)
        return None, ({"msg": _(MSG_NOT_FOUND)}, 404)

    return record, None


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def get_by_id(record_id: str) -> tuple[dict, int]:
    """One public record, summarised exactly as the authenticated detail is.

    ``filepath`` and the raw ``processing`` block do not leave here either -
    more obviously so, since the caller is anonymous.
    """
    record, error = load_public(record_id)
    if error is not None:
        return error

    record["processing"] = services._summarise_processing(record.get("processing"))
    record.pop("filepath", None)
    record["parent"] = services._describe_parents(record.get("parent") or [])
    record.pop("parents", None)

    return services.parse_result(record), 200


def get_by_gallery_index(body: dict) -> tuple[dict, int]:
    """The nth image of a public resource's gallery, in the curator's order."""
    resource_id = body.get("id")
    index = body.get("index")

    if not resource_id:
        return {"msg": _("id is missing")}, 400
    if index is None or isinstance(index, bool) or not isinstance(index, int) or index < 0:
        return {"msg": _("index is missing")}, 400

    resource, error = load_public_resource(resource_id, {"filesObj": 1})
    if error is not None:
        return error

    images = viewers.gallery_records(resource)
    if index >= len(images):
        return {"msg": _(MSG_NOT_FOUND)}, 404

    return get_by_id(str(images[index]["_id"]))


# ---------------------------------------------------------------------------
# Shared with the resources public layer
# ---------------------------------------------------------------------------


def load_public_resource(
    resource_id: str, fields: dict | None = None
) -> tuple[dict | None, tuple[dict, int] | None]:
    """A resource an anonymous caller may see, or a 404-shaped refusal."""
    from archihub.api.resources.access import is_public as resource_is_public

    object_id = services._to_object_id(resource_id)
    if object_id is None:
        return None, ({"msg": _("Resource does not exist")}, 404)

    projection = None
    if fields:
        projection = {**fields, "accessRights": 1, "parents": 1, "status": 1, "post_type": 1}

    resource = _mongo().get_record("resources", {"_id": object_id}, fields=projection)
    if not resource or not resource_is_public(resource):
        return None, ({"msg": _("Resource does not exist")}, 404)

    return resource, None


# ---------------------------------------------------------------------------
# Derived views
# ---------------------------------------------------------------------------


def get_transcription(record_id: str, slug: str, page: int = 0) -> tuple[dict, int]:
    record, error = load_public(record_id)
    if error is not None:
        return error

    try:
        result = transcription.build(record, slug, page)
    except transcription.TranscriptionError as exc:
        return {"msg": str(exc)}, exc.status_code

    return services.parse_result(result), 200


def stream(record_id: str, size: str = "large"):
    """The record's web derivative.

    Served as an attachment, which is what the legacy public route did -
    unlike its authenticated twin, which serves the same bytes inline. The
    inconsistency is preserved rather than smoothed over: changing a
    ``Content-Disposition`` on a public endpoint that external sites may embed
    is a wire change, and it belongs with the coordinated frontend pass rather
    than here. Range support is unaffected either way, so seeking still works.
    """
    record, error = load_public(record_id)
    if error is not None:
        return error

    path, kind = media.derivative_of(record, size)
    if not path.is_file():
        raise media.NotStreamable(_("Record has not been processed"))

    from archihub.core.responses import file_response, guess_media_type

    return file_response(
        path, as_attachment=True, media_type=guess_media_type(path.name)
    )


def download(record_id: str, kind: str):
    """Download a public record's master or derivative.

    The ``files_download`` capability is checked here. The legacy public route
    did not check it at all, while its authenticated twin did - so an archive
    that had switched downloads off still served them to anonymous callers, on
    the one surface where that matters most.
    """
    if not media.downloads_enabled():
        raise media.DownloadRefused(_("Files download isn't active"), 400)

    record, error = load_public(record_id)
    if error is not None:
        return error

    return media.download(record, kind)
