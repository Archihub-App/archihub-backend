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

The Elasticsearch-indexing half of the legacy module lives in
``archihub/worker/tasks/geometries.py`` — it is a Celery task, not a route. The
loader (``upload_shapes``) stays here, because it is what produces the documents
everything else in this module reads.
"""

from __future__ import annotations

import logging
import re as _re

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


def geo_data_directory():
    """Where the bundled administrative boundary files live.

    RESOLVED FROM THIS FILE, not from the working directory. The original did
    ``os.path.abspath('app/utils/geo')``, which is only that directory when the
    process happens to have been started from the repository root - under
    gunicorn with a different working directory it resolved somewhere that does
    not exist, and the route answered 500 with a bare ``FileNotFoundError``.

    The data itself still sits under ``app/`` because it is 24MB of GeoJSON and
    duplicating it during the migration helps nobody. It MOVES TO
    ``archihub/data/geo`` AT PHASE 7 CUTOVER, when ``app/`` is deleted; this
    function and the setting below are the only two places that changes.
    """
    from pathlib import Path

    from archihub.core.settings import get_settings

    configured = getattr(get_settings(), "geo_data_path", "") or ""
    if configured:
        return Path(configured)

    # archihub/api/geosystem/services.py -> repository root
    return Path(__file__).resolve().parents[3] / "app" / "utils" / "geo"


#: ``admin_0``, ``admin_1``, ... Anything else in the directory is ignored.
_ADMIN_DIRECTORY = _re.compile(r"^admin_(\d+)$")


def _boundary_levels(directory) -> list[tuple[int, object]]:
    """The bundled boundary directories, LOWEST ADMINISTRATIVE LEVEL FIRST.

    Both properties matter and neither held before.

    The original iterated ``os.listdir`` and did ``int(f.split('admin_')[1])``
    on every entry. That directory also contains ``world.json``, a plain file,
    for which the split yields a one-element list - so the loader raised
    ``IndexError: list index out of range`` before reading anything, and
    ``/system/geo-load`` returned 500 on the data the application ships with.
    (BACKEND_FINDINGS F47.)

    The order is the second half. Each level's shapes are matched to a parent by
    intersecting them against the level above, which must therefore already be
    in the database. ``os.listdir`` returns entries in filesystem order, so
    whether that held was luck: processing ``admin_1`` before ``admin_0`` gave
    every department a null parent, and the explore map's drill-down silently
    stopped working one level down.
    """
    levels = []
    for entry in sorted(directory.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        match = _ADMIN_DIRECTORY.match(entry.name)
        if match:
            levels.append((int(match.group(1)), entry))
    return sorted(levels, key=lambda pair: pair[0])


def upload_shapes() -> tuple[dict, int]:
    """Load the bundled administrative boundaries into MongoDB.

    Each level replaces itself: the existing shapes at that level are deleted
    and the file's features inserted, with each feature matched to its parent at
    the level above. Called synchronously from ``/system/geo-load`` — see the
    note on that route about why it still is.
    """
    directory = geo_data_directory()
    if not directory.is_dir():
        logger.error("Boundary data directory not found: %s", directory)
        return {"msg": _("Error updating the polygons")}, 500

    try:
        loaded = 0
        for level, folder in _boundary_levels(directory):
            for path in sorted(folder.iterdir()):
                if path.suffix != ".json":
                    continue
                loaded += _load_boundary_file(path, level)

        logger.info("Loaded %d boundary shapes", loaded)
        return {"msg": _("Shapes uploaded successfully")}, 200
    except Exception:
        # The real reason names a file path on the server's disk.
        logger.exception("Could not load boundary shapes")
        return {"msg": _("Error updating the polygons")}, 500


def _load_boundary_file(path, level: int) -> int:
    """Replace one administrative level from one GeoJSON file."""
    import json

    import geopandas as gpd

    from archihub.api.geosystem.models import Polygon

    with path.open() as handle:
        data = json.load(handle)

    features = gpd.GeoDataFrame.from_features(data)
    for required in ("ident", "name"):
        if required not in features.columns:
            raise ValueError(f"{path.name}: boundary features have no {required!r} property")

    features["admin_level"] = level
    features["name"] = features["name"].str.capitalize()
    if features.crs is None:
        features.crs = "EPSG:4326"

    mongo = _mongo()
    mongo.delete_records(COLLECTION, {"properties.admin_level": level})

    if level > 0:
        _attach_parents(features, level, gpd)

    rows = json.loads(features.to_json())["features"]
    documents = []
    for feature in rows:
        # Nulls are dropped rather than stored: a null `parent` is queried with
        # `{'properties.parent': value}` elsewhere, and an absent key and a
        # stored null do not behave the same way there.
        feature["properties"] = {k: v for k, v in feature["properties"].items() if v is not None}
        documents.append(Polygon(**feature))

    mongo.insert_records(COLLECTION, documents)
    return len(documents)


def _attach_parents(features, level: int, gpd) -> None:
    """Fill each feature's ``parent``/``parent_name`` from the level above."""
    parents = list(
        _mongo().get_all_records(
            COLLECTION,
            {"properties.admin_level": level - 1},
            fields={"_id": 1, "geometry": 1, "properties.ident": 1, "properties.name": 1},
        )
    )
    if not parents:
        logger.warning("No level-%d boundaries stored; level %d will have no parents", level - 1, level)
        features["parent"] = None
        features["parent_name"] = None
        return

    parent_frame = gpd.GeoDataFrame.from_features(
        [
            {
                "type": "Feature",
                "geometry": parent["geometry"],
                "properties": {
                    "parent_ident": parent["properties"]["ident"],
                    "parent_name": parent["properties"]["name"],
                },
            }
            for parent in parents
        ]
    )
    if parent_frame.crs is None:
        parent_frame.crs = "EPSG:4326"

    # Match on centroids first: a child lies within exactly one parent, so this
    # gives one answer per shape.
    centroids = features.copy()
    centroids.geometry = centroids.centroid
    joined = gpd.sjoin(centroids, parent_frame, how="left", predicate="within")

    # Anything whose centroid fell outside every parent - which simplified
    # borders and coastal shapes routinely do - is matched by intersection
    # instead, keeping the first parent it overlaps.
    missing = joined["parent_ident"].isna()
    if missing.any():
        overlap = gpd.sjoin(features[missing], parent_frame, how="inner", predicate="intersects")
        overlap = overlap[~overlap.index.duplicated(keep="first")]
        joined.loc[overlap.index, "parent_ident"] = overlap["parent_ident"]
        joined.loc[overlap.index, "parent_name"] = overlap["parent_name"]

    features["parent"] = joined["parent_ident"].where(joined["parent_ident"].notna(), None)
    features["parent_name"] = joined["parent_name"].where(joined["parent_name"].notna(), None)


def get_shape_centroid(ident: str, parent: str | None, level: int) -> list[dict] | None:
    """The centre point(s) of a named administrative boundary, as GeoJSON.

    Used by the search indexer: a resource whose location is recorded as
    "this municipality" is indexed at the municipality's centroid, so it can be
    found by a map query. A MultiPolygon yields one point PER PART rather than
    one for the whole - an archipelago's overall centroid can fall in open
    water, hundreds of kilometres from any of its land.

    Returns ``None`` when the boundary is not stored. The original raised
    ``Exception(f'Error al obtener el centroide ...')`` for any failure
    including a simple miss, and that exception propagated into the indexing
    loop's swallow-everything handler - so a resource referring to a boundary
    this instance has not loaded silently disappeared from the search index.
    """
    if not ident:
        return None

    from shapely.geometry import mapping, shape

    filters: dict = {"properties.admin_level": level, "properties.ident": ident}
    if parent:
        filters["properties.parent"] = parent

    document = _mongo().get_record(
        "shapes",
        filters,
        fields={"geometry": 1, "properties.name": 1, "properties.ident": 1},
    )
    if not document or not document.get("geometry"):
        return None

    geometry = document["geometry"]
    try:
        if geometry.get("type") == "MultiPolygon":
            return [
                mapping(shape({"type": "Polygon", "coordinates": part}).centroid)
                for part in geometry.get("coordinates") or []
            ]
        return [mapping(shape(geometry).centroid)]
    except Exception:
        logger.warning("Could not compute a centroid for boundary %r", ident)
        return None


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
