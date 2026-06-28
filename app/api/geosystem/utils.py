import geopandas as gpd
import topojson as tp
from flask_babel import gettext as _

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

def simplify_geojson(input_path, output_path, retention_percentage=0.20):
    """
    Simplifies a GeoJSON mimicking Mapshaper's behavior:
    - Algorithm: Visvalingam-Whyatt
    - Prevents 'shape removal' (prevents polygons from completely disappearing)
    - Maintains topology (shared borders intact)
    - Exact control by retention percentage (0.0 to 1.0)
    """
    print(_("Loading GeoJSON..."))
    gdf = gpd.read_file(input_path)
    original_vertices = count_vertices(gdf)
    target_vertices = original_vertices * retention_percentage
    
    print(_("Building topology..."))
    # topology=True prevents gaps; prequantize=False maintains accurate coordinates
    topo = tp.Topology(gdf, topology=True, prequantize=False)
    
    print(_("Searching for the factor to retain ~%(percentage)s%% of vertices...", percentage=retention_percentage*100))
    # Search range for the area threshold (epsilon)
    eps_min, eps_max = 0.0, 10.0 
    best_gdf = gdf
    
    # 15 iterations are usually enough to find the exact percentage
    for _iter in range(15): 
        eps_mid = (eps_min + eps_max) / 2
        
        # prevent_oversimplify=True is the equivalent to "keep-shapes" in mapshaper
        topo_simp = topo.toposimplify(
            epsilon=eps_mid,
            simplify_algorithm='vw', 
            simplify_with='simplification',
            prevent_oversimplify=True
        )
        
        gdf_temp = topo_simp.to_gdf()
        current_vertices = count_vertices(gdf_temp)
        
        if current_vertices > target_vertices:
            eps_min = eps_mid # Need to simplify more (raise the minimum area)
        else:
            eps_max = eps_mid # Simplified too much (lower the minimum area)
            best_gdf = gdf_temp
            
    print(_("Exporting result..."))
    best_gdf.to_file(output_path, driver="GeoJSON")
    print(_("Completed!"))