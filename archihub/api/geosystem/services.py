"""Administrative boundary shapes.

Both routes here are **unauthenticated**, and that is deliberate — the public
explore map draws these before anyone has signed in, and the frontend calls
`/geosystem/level` with no `Authorization` header at all. They are reference
geography, not archive content.

Being unauthenticated is what shapes this module. Every parameter arrives from
an anonymous caller and each one reaches either a Mongo query or an O(n log n)
geometry pass over megabytes of coordinates, so:

* **Nothing from the request becomes a query operator.** `ident`, `parent` and
  `type` are required to be strings. The originals assigned them into the filter
  as-is, so a JSON object arrived as a Mongo operator — `{"ident": {"$ne": null}}`
  returns every shape in the collection, simplifies all of them, and does it
  without an account. Recorded as BACKEND_FINDINGS S27.
* **Results are capped.** Asking without an `ident` returned every matching
  shape, unbounded, each one simplified.
* **Retention is quantised** before it reaches the simplification cache; see
  ``simplify.normalise_retention``.

The upload and Elasticsearch-indexing halves of the legacy module are not ported
here: they have no routes, and `index_shapes` belongs with `search`.
"""

from __future__ import annotations

import logging

from archihub.api.geosystem import simplify as simplifier
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

COLLECTION = "shapes"

#: Most shapes a single request may return. A country's third administrative
#: level runs to thousands of polygons; simplifying all of them for one
#: anonymous request is the denial of service, not the feature.
MAX_SHAPES = 500

#: Administrative levels that exist. Anything outside is a malformed request,
#: not an empty result, and saying so beats returning `[]` for a typo.
MIN_LEVEL = 0
MAX_LEVEL = 5

#: Bounding-box area below which the level is overridden, and the retention used
#: at each step. Preserved from the original, whose intent is "zoomed in far
#: enough to want detail".
DETAIL_AREA = 400
CLOSE_AREA = 40

MSG_NOT_FOUND = "Shape not found"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def parse_result(result):
    import json

    from bson import json_util

    return json.loads(json_util.dumps(result))


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


class InvalidQuery(Exception):
    """The request does not describe a shape query that can be run."""


def _identifier(value, field: str):
    """A shape identifier from the request, or ``None``.

    **Must be a string.** This is the whole of the injection defence: a dict
    here becomes a Mongo operator, and the collection is queryable by anyone.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidQuery(_('"{field}" must be a text value', field=field))
    return value


def _level(value, *, required: bool = True) -> int | None:
    if value is None:
        if required:
            raise InvalidQuery(_("You must specify a level"))
        return None

    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InvalidQuery(_("You must specify a level"))
    try:
        level = int(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError is the one that is easy to miss: `int(1e400)` raises it,
        # not ValueError, so an anonymous caller sending a huge float got a 500.
        raise InvalidQuery(_("You must specify a level")) from None

    if not MIN_LEVEL <= level <= MAX_LEVEL:
        raise InvalidQuery(_("You must specify a level"))
    return level


def _bounds(value) -> dict | None:
    """A viewport rectangle, validated as four numbers in range.

    These become the coordinates of a ``$geoIntersects`` polygon. The original
    used them unchecked, so a non-numeric one reached Mongo and came back as a
    500 carrying the driver's error text.
    """
    if not value:
        return None
    if not isinstance(value, dict):
        raise InvalidQuery(_("Invalid bounds"))

    corners = {}
    for key, limit in (("minLng", 180), ("maxLng", 180), ("minLat", 90), ("maxLat", 90)):
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise InvalidQuery(_("Invalid bounds"))
        if not -limit <= float(item) <= limit:
            raise InvalidQuery(_("Invalid bounds"))
        corners[key] = float(item)

    if corners["minLng"] >= corners["maxLng"] or corners["minLat"] >= corners["maxLat"]:
        raise InvalidQuery(_("Invalid bounds"))
    return corners


def _threshold(value) -> float:
    try:
        threshold = float(value or 0)
    except (TypeError, ValueError):
        raise InvalidQuery(_("Invalid area threshold")) from None
    if threshold < 0:
        raise InvalidQuery(_("Invalid area threshold"))
    return threshold


# ---------------------------------------------------------------------------
# One level of the map
# ---------------------------------------------------------------------------


def get_level(body: dict) -> tuple[list | dict, int]:
    """The shapes at one administrative level, optionally within a viewport."""
    try:
        level = _level(body.get("level", 0))
        parent = _identifier(body.get("parent"), "parent")
        bounds = _bounds(body.get("bounds"))
        threshold = 4.0 if level == 0 else _threshold(body.get("area_threshold"))
    except InvalidQuery as exc:
        return {"msg": str(exc)}, 400

    filters: dict = {"properties.admin_level": level}
    if parent is not None:
        filters["properties.parent"] = parent

    if bounds is not None:
        area = abs(bounds["maxLng"] - bounds["minLng"]) * abs(bounds["maxLat"] - bounds["minLat"])

        if CLOSE_AREA < area < DETAIL_AREA:
            # The original wrote `{'$gte': level}` and then immediately
            # overwrote the same key with `{'$lt': level + 2}`, so the lower
            # bound never applied and levels *below* the requested one came
            # back. Both bounds are in one expression here.
            filters["properties.admin_level"] = {"$gte": level, "$lt": level + 2}
            threshold = 0.1
        elif area <= CLOSE_AREA:
            filters["properties.admin_level"] = 2
            threshold = 0.01

        filters["geometry"] = {"$geoIntersects": {"$geometry": _viewport(bounds)}}

    shapes = list(
        _mongo().get_all_records(
            COLLECTION,
            filters,
            fields={"geometry": 1, "properties.name": 1, "properties.ident": 1},
            sort=[("properties.admin_level", 1), ("properties.name", 1)],
            limit=MAX_SHAPES,
        )
    )

    return parse_result([s for s in (_prepare(s, threshold, level, bounds) for s in shapes) if s]), 200


def _viewport(bounds: dict) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [bounds["minLng"], bounds["minLat"]],
                [bounds["maxLng"], bounds["minLat"]],
                [bounds["maxLng"], bounds["maxLat"]],
                [bounds["minLng"], bounds["maxLat"]],
                [bounds["minLng"], bounds["minLat"]],
            ]
        ],
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
    }


def _prepare(document: dict, threshold: float, level: int, bounds: dict | None) -> dict | None:
    """Drop the slivers, add a centroid, and thin the outline. ``None`` to skip."""
    from shapely.geometry import MultiPolygon, mapping, shape

    try:
        geometry = shape(document["geometry"])
    except Exception:
        # A malformed stored geometry drops out of the layer rather than taking
        # the whole map request down, which is what the original's single
        # try/except around everything did.
        logger.warning("Unusable stored geometry on shape %s", document.get("_id"))
        return None

    if geometry.geom_type == "Polygon":
        if geometry.area < threshold:
            return None
        usable = geometry
    elif geometry.geom_type == "MultiPolygon":
        parts = [part for part in geometry.geoms if part.area >= threshold]
        if not parts:
            return None
        usable = parts[0] if len(parts) == 1 else MultiPolygon(parts)
    else:
        return None

    document.pop("_id", None)
    document["centroid"] = mapping(usable.centroid)
    tolerance = 1 if (level == 0 and not bounds) else 0
    document["geometry"] = mapping(usable.simplify(tolerance, preserve_topology=True))
    return document


# ---------------------------------------------------------------------------
# One shape, or the shapes under a parent
# ---------------------------------------------------------------------------


def get_shape(body: dict) -> tuple[list | dict, int]:
    """A shape's outline by identifier, or every shape matching the filters.

    ``type: 'administrative'`` is a special request meaning "the first-level
    divisions of this shape": the identifier becomes the parent and the level is
    forced to 1. Preserved from the original.
    """
    try:
        ident = _identifier(body.get("ident"), "ident")
        parent = _identifier(body.get("parent"), "parent")
        shape_type = _identifier(body.get("type"), "type")
        level = _level(body.get("level"), required=False)
    except InvalidQuery as exc:
        return {"msg": str(exc)}, 400

    if shape_type == "administrative":
        shape_type = None
        level = 1
        if ident and not parent:
            parent = ident
        ident = None

    if level is None:
        return {"msg": _("You must specify a level")}, 400

    retention = simplifier.normalise_retention(body.get("retention", 0.1))

    filters: dict = {
        "properties.admin_level": level,
        "properties.shape_type": shape_type if shape_type else {"$exists": False},
    }
    if ident:
        filters["properties.ident"] = ident
    if parent:
        filters["properties.parent"] = parent

    fields = {"geometry": 1, "properties.name": 1, "properties.ident": 1, "type": 1}

    if ident:
        document = _mongo().get_record(COLLECTION, filters, fields=fields)
        if not document:
            return {"msg": _(MSG_NOT_FOUND)}, 404
        document.pop("_id", None)
        return parse_result(_simplified([document], retention)[0]), 200

    documents = list(_mongo().get_all_records(COLLECTION, filters, fields=fields, limit=MAX_SHAPES))
    for document in documents:
        document.pop("_id", None)

    return parse_result(_simplified(documents, retention)), 200


def _simplified(documents: list[dict], retention: float) -> list[dict]:
    """Coerce each feature to a polygon, thin it, and repair what that broke."""
    if not documents:
        return []

    from shapely.geometry import mapping, shape

    collection = {
        "type": "FeatureCollection",
        "features": [ensure_polygon(document) for document in documents],
    }
    simplified = simplifier.simplify(collection, retention)

    repaired = []
    for feature in simplified.get("features") or []:
        geometry = feature.get("geometry")
        if geometry:
            try:
                geom = shape(geometry)
                if not geom.is_valid:
                    geom = geom.buffer(0)
                feature["geometry"] = mapping(geom)
            except Exception:
                logger.warning("Could not repair a simplified geometry; serving it as is")
        repaired.append(feature)

    return repaired


def ensure_polygon(feature: dict) -> dict:
    """Coerce a stored feature's geometry to a polygon.

    Boundary data arrives as lines as often as as areas, and the map draws
    filled regions - so open rings are merged and closed before anything else
    touches them. Simplification can break an open ring badly enough that
    polygonising afterwards fails, which is why this runs first.
    """
    from shapely.geometry import LinearRing, MultiPolygon
    from shapely.geometry import Polygon as ShapelyPolygon
    from shapely.geometry import mapping, shape
    from shapely.ops import linemerge, polygonize

    geometry = feature.get("geometry")
    if not geometry:
        return feature

    try:
        geom = shape(geometry)
    except Exception:
        logger.warning("Unusable stored geometry; leaving the feature alone")
        return feature

    if geom.geom_type in ("Polygon", "MultiPolygon"):
        feature["geometry"] = mapping(geom.buffer(0))
        return feature

    if geom.geom_type not in ("LineString", "MultiLineString"):
        feature["geometry"] = mapping(geom.buffer(0.001))
        return feature

    # `linemerge` raises for a bare LineString in shapely 2.x - it wants a
    # collection to merge. The original called it unconditionally, so a boundary
    # stored as one continuous line raised `ValueError: Cannot linemerge ...`
    # and the request 500'd. There is nothing to merge in that case anyway.
    merged = geom if geom.geom_type == "LineString" else linemerge(geom)
    polygons = list(polygonize(merged if merged.geom_type != "LineString" else [merged]))

    if not polygons:
        # Polygonising failed, usually because the ring has a small gap. Try
        # closing each line by hand before giving up.
        lines = [merged] if merged.geom_type == "LineString" else list(merged.geoms)
        for line in lines:
            coords = list(line.coords)
            if len(coords) < 3:
                continue
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            try:
                candidate = ShapelyPolygon(LinearRing(coords))
            except Exception:
                continue
            if candidate.is_valid and candidate.area > 0:
                polygons.append(candidate)

    if not polygons:
        # Last resort: a thin buffer around the line, so the caller gets a
        # drawable shape rather than nothing.
        feature["geometry"] = mapping(geom.buffer(0.001))
        return feature

    feature["geometry"] = mapping(polygons[0] if len(polygons) == 1 else MultiPolygon(polygons))
    return feature
