"""Visvalingam/weighted-area simplification.

Ported verbatim from ``app/api/geosystem/utils.py`` - it is pure geometry with
no framework coupling, and reimplementing a working line-simplification
algorithm during a framework port would be inviting a subtly different map.

The weighting is Mapshaper's: points at sharp angles are underweighted so a
spike survives simplification where a gentle bend of the same triangle area does
not. ``weight_factor=0.7`` is Mapshaper's default.

What is NOT ported verbatim is the caching around it - see ``simplify`` in this
module and BACKEND_FINDINGS P3.
"""

from __future__ import annotations

import heapq
import logging
import math

logger = logging.getLogger(__name__)


def _triangle_area(ax, ay, bx, by, cx, cy):
    """Signed area of triangle ABC (absolute value)."""
    return abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) / 2.0


def _angle_weight(ax, ay, bx, by, cx, cy, weight_factor=0.7):
    """
    Mapshaper weighted-area factor: underweight points at sharp angles.
    Returns a multiplier in (0, 1] that penalizes sharp bends.
    weight_factor=0.7 matches mapshaper's default.
    """
    # Vectors BA and BC
    dxba = ax - bx
    dyba = ay - by
    dxbc = cx - bx
    dybc = cy - by
    
    dot = dxba * dxbc + dyba * dybc
    mag_ba = math.sqrt(dxba * dxba + dyba * dyba)
    mag_bc = math.sqrt(dxbc * dxbc + dybc * dybc)
    
    if mag_ba == 0 or mag_bc == 0:
        return 0.0
    
    cos_angle = max(-1.0, min(1.0, dot / (mag_ba * mag_bc)))
    # cos_angle = -1 means straight (180°), cos_angle = 1 means sharp spike (0°)
    # Mapshaper: weight = cos_angle * 0.5 + 0.5 gives 0 for spikes, 1 for straight
    # Then raised to weight_factor power
    w = (cos_angle * 0.5 + 0.5)
    if w <= 0:
        return 0.0
    return w ** weight_factor


def _visvalingam_weighted(coords, target_count, min_points=4):
    """
    Visvalingam/weighted area simplification on a single ring.
    
    coords: list of (x, y) tuples (closed ring — first == last)
    target_count: desired number of output vertices (including closing vertex)
    min_points: minimum vertices to keep (prevents shape disappearance)
    
    Returns simplified list of (x, y) tuples (closed ring).
    """
    n = len(coords)
    # For closed rings, we work with n-1 points (skip duplicate closing point)
    is_closed = (n > 1 and coords[0] == coords[-1])
    if is_closed:
        pts = list(coords[:-1])
    else:
        pts = list(coords)
    
    n = len(pts)
    if n <= min_points:
        if is_closed:
            return pts + [pts[0]]
        return pts
    
    # Target for the open ring (minus closing vertex)
    target_open = max(min_points, target_count - (1 if is_closed else 0))
    if target_open >= n:
        if is_closed:
            return pts + [pts[0]]
        return pts
    
    # Build doubly-linked list
    REMOVED = -1
    prev_arr = list(range(-1, n - 1))  # prev_arr[i] = i-1
    next_arr = list(range(1, n + 1))   # next_arr[i] = i+1
    
    # For closed rings, link first and last
    if is_closed:
        prev_arr[0] = n - 1
        next_arr[n - 1] = 0
    
    def weighted_area(i):
        """Compute weighted area for point i."""
        p = prev_arr[i]
        nx = next_arr[i]
        if p == REMOVED or nx == REMOVED:
            return float('inf')
        if not is_closed and (p < 0 or nx >= n):
            return float('inf')  # endpoints
        
        area = _triangle_area(
            pts[p][0], pts[p][1],
            pts[i][0], pts[i][1],
            pts[nx][0], pts[nx][1]
        )
        w = _angle_weight(
            pts[p][0], pts[p][1],
            pts[i][0], pts[i][1],
            pts[nx][0], pts[nx][1]
        )
        return area * w
    
    # Initialize heap: (weighted_area, index)
    heap = []
    for i in range(n):
        if not is_closed and (i == 0 or i == n - 1):
            continue  # don't remove endpoints of open lines
        wa = weighted_area(i)
        heapq.heappush(heap, (wa, i))
    
    alive = n
    removed = set()
    
    while alive > target_open and heap:
        wa, i = heapq.heappop(heap)
        if i in removed:
            continue
        
        # Check if area is still current (lazy deletion)
        current_wa = weighted_area(i)
        if abs(current_wa - wa) > 1e-15:
            heapq.heappush(heap, (current_wa, i))
            continue
        
        # Remove point i
        removed.add(i)
        alive -= 1
        
        # Relink
        p = prev_arr[i]
        nx = next_arr[i]
        if p != REMOVED and p >= 0:
            next_arr[p] = nx
        if nx != REMOVED and nx < n:
            prev_arr[nx] = p
        prev_arr[i] = REMOVED
        next_arr[i] = REMOVED
        
        # Recalculate neighbors
        if p not in removed and p >= 0:
            if is_closed or (p > 0 and p < n - 1):
                new_wa = weighted_area(p)
                heapq.heappush(heap, (new_wa, p))
        if nx not in removed and nx < n:
            if is_closed or (nx > 0 and nx < n - 1):
                new_wa = weighted_area(nx)
                heapq.heappush(heap, (new_wa, nx))
    
    result = [pts[i] for i in range(n) if i not in removed]
    if is_closed and len(result) > 0:
        result.append(result[0])
    return result


def _simplify_ring(coords, retention):
    """Simplify a single ring by retention percentage."""
    n = len(coords)
    target = max(4, int(math.ceil(n * retention)))
    return _visvalingam_weighted(coords, target)


def _simplify_geometry(geom_dict, retention):
    """Simplify a GeoJSON geometry dict in place."""
    geom_type = geom_dict.get('type')
    coords = geom_dict.get('coordinates')
    
    if geom_type == 'Polygon':
        new_rings = []
        for ring in coords:
            tuples = [tuple(c) for c in ring]
            simplified = _simplify_ring(tuples, retention)
            new_rings.append([list(c) for c in simplified])
        geom_dict['coordinates'] = new_rings
        
    elif geom_type == 'MultiPolygon':
        new_polys = []
        for polygon_rings in coords:
            new_rings = []
            for ring in polygon_rings:
                tuples = [tuple(c) for c in ring]
                simplified = _simplify_ring(tuples, retention)
                new_rings.append([list(c) for c in simplified])
            new_polys.append(new_rings)
        geom_dict['coordinates'] = new_polys
        
    elif geom_type == 'LineString':
        tuples = [tuple(c) for c in coords]
        n = len(tuples)
        target = max(2, int(math.ceil(n * retention)))
        simplified = _visvalingam_weighted(tuples, target, min_points=2)
        geom_dict['coordinates'] = [list(c) for c in simplified]
        
    elif geom_type == 'MultiLineString':
        new_lines = []
        for line in coords:
            tuples = [tuple(c) for c in line]
            n = len(tuples)
            target = max(2, int(math.ceil(n * retention)))
            simplified = _visvalingam_weighted(tuples, target, min_points=2)
            new_lines.append([list(c) for c in simplified])
        geom_dict['coordinates'] = new_lines
    
    return geom_dict


def _count_geom_vertices(geom):
    """Count vertices in a shapely geometry."""
    total = 0
    if geom is None:
        return 0
    if geom.geom_type == 'Polygon':
        total += len(geom.exterior.coords)
        for interior in geom.interiors:
            total += len(interior.coords)
    elif geom.geom_type == 'MultiPolygon':
        for part in geom.geoms:
            total += len(part.exterior.coords)
            for interior in part.interiors:
                total += len(interior.coords)
    elif geom.geom_type == 'LineString':
        total += len(geom.coords)
    elif geom.geom_type == 'MultiLineString':
        for part in geom.geoms:
            total += len(part.coords)
    return total


# ---------------------------------------------------------------------------
# Simplifying a whole feature collection, with a bounded disk cache
# ---------------------------------------------------------------------------

#: Retention is rounded to this many decimals before it reaches the cache key.
#: The original took the client's float verbatim and wrote one cache file per
#: distinct value, on an **unauthenticated** route - so a caller could fill the
#: temporal volume by walking `0.100001, 0.100002, ...`. Quantising bounds the
#: key space to a hundred entries per shape set; sweeping bounds it in time.
#: See BACKEND_FINDINGS P3.
RETENTION_DECIMALS = 2
MIN_RETENTION = 0.01
MAX_RETENTION = 1.0

CACHE_DIRECTORY = "geojson"
CACHE_PREFIX = "simplified-"
STALE_CACHE_SECONDS = 7 * 24 * 60 * 60


def normalise_retention(value) -> float:
    """A retention fraction the caller may ask for, clamped and quantised."""
    try:
        retention = float(value)
    except (TypeError, ValueError):
        return 0.1
    if math.isnan(retention) or math.isinf(retention):
        return 0.1
    retention = min(max(retention, MIN_RETENTION), MAX_RETENTION)
    return round(retention, RETENTION_DECIMALS)


def _cache_directory():
    from pathlib import Path

    from archihub.core.settings import get_settings

    root = get_settings().temporal_files_path
    if not root:
        return None
    directory = Path(root) / CACHE_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def sweep_stale_cache(directory) -> int:
    """Drop simplified geometry nobody has asked for in a week.

    It is derivable from the shapes collection at any time, so keeping it is a
    cache decision. The original never removed anything.
    """
    import time
    from pathlib import Path

    cutoff = time.time() - STALE_CACHE_SECONDS
    removed = 0
    try:
        entries = list(Path(directory).iterdir())
    except OSError:
        return 0

    for entry in entries:
        if not entry.name.startswith(CACHE_PREFIX):
            continue
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            logger.debug("Could not remove a stale geometry cache file", exc_info=True)

    if removed:
        logger.info("Removed %d stale simplified-geometry file(s)", removed)
    return removed


def simplify(feature_collection: dict, retention) -> dict:
    """Simplify every feature, reusing a cached result when there is one.

    Returns a new collection; the input is not mutated.
    """
    import copy
    import hashlib
    import json

    from shapely.geometry import mapping, shape

    retention = normalise_retention(retention)
    directory = _cache_directory()
    cache_path = None

    if directory is not None:
        sweep_stale_cache(directory)
        digest = hashlib.sha256(
            (json.dumps(feature_collection, sort_keys=True) + str(retention)).encode("utf-8")
        ).hexdigest()
        cache_path = directory / f"{CACHE_PREFIX}{digest}.geojson"

        if cache_path.is_file():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.info("Discarding an unreadable geometry cache entry")

    result = copy.deepcopy(feature_collection)

    for feature in result.get("features") or []:
        geometry = feature.get("geometry")
        if not geometry:
            continue

        original = shape(geometry)
        _simplify_geometry(geometry, retention)

        try:
            simplified = shape(geometry)
            if not simplified.is_valid:
                simplified = simplified.buffer(0)
                feature["geometry"] = mapping(simplified)
        except Exception:
            # Simplification broke the geometry - keep the original rather than
            # returning something that will not draw.
            logger.info("Simplification produced an invalid geometry; keeping the original")
            feature["geometry"] = mapping(original)

    if cache_path is not None:
        try:
            staging = cache_path.with_suffix(".partial")
            staging.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            staging.replace(cache_path)
        except OSError:
            logger.warning("Could not write the geometry cache entry", exc_info=True)

    return result
