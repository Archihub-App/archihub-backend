import geopandas as gpd
from flask_babel import gettext as _
import json
import hashlib
import os
import math
import heapq
from shapely.geometry import shape, mapping, Polygon, MultiPolygon


def count_vertices(gdf):
    """Counts the total valid coordinates in a GeoDataFrame."""
    total = 0
    for geom in gdf.geometry:
        if geom is None:
            continue
        if geom.geom_type in ['Polygon', 'LineString']:
            if geom.geom_type == 'Polygon':
                total += len(geom.exterior.coords)
                for interior in geom.interiors:
                    total += len(interior.coords)
            else:
                total += len(geom.coords)
        elif geom.geom_type in ['MultiPolygon', 'MultiLineString']:
            for part in geom.geoms:
                if part.geom_type == 'Polygon':
                    total += len(part.exterior.coords)
                    for interior in part.interiors:
                        total += len(interior.coords)
                else:
                    total += len(part.coords)
    return total


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


def simplify_geojson(geojson_dict, retention_percentage=0.20):
    """
    Simplifies a GeoJSON using Visvalingam/weighted area (Mapshaper-style):
    - Underweights points at sharp angles (weight=0.7, same as mapshaper default)
    - Uses min-heap for O(n log n) performance
    - Prevents shape removal (minimum 4 vertices per ring)
    - retention_percentage: fraction of vertices to keep (0.0 to 1.0)
    """
    TEMPORAL_FILES_PATH = os.environ.get('TEMPORAL_FILES_PATH', 'temporal')
    cache_dir = os.path.join(TEMPORAL_FILES_PATH, 'geojson')
    os.makedirs(cache_dir, exist_ok=True)
    
    geojson_str = json.dumps(geojson_dict, sort_keys=True)
    hash_obj = hashlib.md5((geojson_str + str(retention_percentage)).encode('utf-8'))
    cache_filename = f"{hash_obj.hexdigest()}.geojson"
    cache_filepath = os.path.join(cache_dir, cache_filename)
    
    if os.path.exists(cache_filepath):
        print(_("Loading from cache..."))
        with open(cache_filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    print(_("Simplifying GeoJSON (Visvalingam weighted area, retention=%(pct)s%%)...",
            pct=round(retention_percentage * 100, 1)))
    
    import copy
    result = copy.deepcopy(geojson_dict)
    
    features = result.get('features', [])
    original_total = 0
    final_total = 0
    
    for feature in features:
        geom = feature.get('geometry')
        if not geom:
            continue
        
        # Count original
        g = shape(geom)
        original_total += _count_geom_vertices(g)
        
        # Simplify
        _simplify_geometry(geom, retention_percentage)
        
        # Validate the result with shapely
        try:
            g_new = shape(geom)
            if not g_new.is_valid:
                g_new = g_new.buffer(0)
                feature['geometry'] = mapping(g_new)
            final_total += _count_geom_vertices(g_new)
        except Exception:
            # If simplification broke it, keep original
            feature['geometry'] = mapping(g)
            final_total += _count_geom_vertices(g)
    
    print(_("Vertices: %(orig)s → %(final)s (%(pct)s%%)",
            orig=original_total, final=final_total,
            pct=round(final_total / max(original_total, 1) * 100, 1)))
    print(_("Completed!"))
    
    with open(cache_filepath, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False)
    
    return result


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