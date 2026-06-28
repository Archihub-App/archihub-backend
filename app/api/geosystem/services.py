from app.utils import CacheHandler
from app.utils import DatabaseHandler, IndexHandler
from app.api.geosystem.models import Polygon
import os
import json
from shapely.geometry import shape, mapping, MultiPolygon
from flask_babel import _
from celery import shared_task
from .utils import simplify_geojson

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()
index_handler = IndexHandler.IndexHandler()
ELASTIC_INDEX_PREFIX = os.environ.get('ELASTIC_INDEX_PREFIX', '')

def update_cache():
    get_level.invalidate_all()
    get_level_info.invalidate_all()
    get_shape_centroid.invalidate_all()
    get_shape_by_ident.invalidate_all()

def upload_shapes():
    try:
        path = 'app/utils/geo'
        path = os.path.abspath(path)
        admin_folders = os.listdir(path)
        admin_level = 0

        for f in admin_folders:
            level = int(f.split('admin_')[1])
            admin_folder_path = os.path.join(path, f)
            for shape_ in os.listdir(admin_folder_path):
                # open json file
                with open(os.path.join(admin_folder_path, shape_)) as json_file:
                    data = json.load(json_file)
                    
                    import geopandas as gpd
                    
                    gdf_features = gpd.GeoDataFrame.from_features(data)
                    
                    if 'ident' not in gdf_features.columns:
                        raise Exception('Ident not found in properties')
                    if 'name' not in gdf_features.columns:
                        raise Exception('Name not found in properties')
                        
                    gdf_features['admin_level'] = level
                    gdf_features['name'] = gdf_features['name'].str.capitalize()
                    
                    # Ensure CRS is set so sjoin doesn't complain
                    if gdf_features.crs is None:
                        gdf_features.crs = "EPSG:4326"

                    mongodb.delete_records('shapes', {'properties.admin_level': level})
                    
                    if level > 0:
                        parent_records = list(mongodb.get_all_records('shapes',
                                                {'properties.admin_level': level - 1},
                                                fields={'_id': 1, 'geometry': 1, 'properties.ident': 1, 'properties.name': 1}
                                            ))
                        
                        parent_features = []
                        for parent in parent_records:
                            parent_features.append({
                                'type': 'Feature',
                                'geometry': parent['geometry'],
                                'properties': {
                                    'parent_ident': parent['properties']['ident'],
                                    'parent_name': parent['properties']['name']
                                }
                            })
                            
                        gdf_parents = gpd.GeoDataFrame.from_features(parent_features)
                        if gdf_parents.crs is None:
                            gdf_parents.crs = "EPSG:4326"
                            
                        # Use centroids to match parents
                        gdf_features_centroids = gdf_features.copy()
                        gdf_features_centroids.geometry = gdf_features_centroids.centroid
                        
                        joined = gpd.sjoin(gdf_features_centroids, gdf_parents, how='left', predicate='within')
                        
                        # Fallback for shapes not matched by centroid
                        missing_mask = joined['parent_ident'].isna()
                        if missing_mask.any():
                            missing_features = gdf_features[missing_mask]
                            joined_missing = gpd.sjoin(missing_features, gdf_parents, how='inner', predicate='intersects')
                            joined_missing = joined_missing[~joined_missing.index.duplicated(keep='first')]
                            
                            joined.loc[joined_missing.index, 'parent_ident'] = joined_missing['parent_ident']
                            joined.loc[joined_missing.index, 'parent_name'] = joined_missing['parent_name']
                            
                        gdf_features['parent'] = joined['parent_ident']
                        gdf_features['parent_name'] = joined['parent_name']
                        
                        # Drop nulls so we don't insert NaN values in MongoDB
                        # Some polygons might not intersect any parent depending on simplified borders
                        gdf_features['parent'] = gdf_features['parent'].where(gdf_features['parent'].notna(), None)
                        gdf_features['parent_name'] = gdf_features['parent_name'].where(gdf_features['parent_name'].notna(), None)

                    # Export to dictionary
                    features_dict = json.loads(gdf_features.to_json())['features']
                    
                    for feature in features_dict:
                        # Clean up NaN/null values from properties if they were inserted
                        feature['properties'] = {k: v for k, v in feature['properties'].items() if v is not None}
                        mongodb.insert_record('shapes', Polygon(**feature))

                    get_level.invalidate_all()
                        

        return {'msg': _('Shapes uploaded successfully')}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
@shared_task(ignore_result=False, name='geosystem.regenerate_index_shapes')
def regenerate_index_shapes():
    mapping = {
        'properties': {
            'geometry': {
                'type': 'geo_shape'
            },
            'properties': {
                'type': 'object',
                'properties': {
                    'admin_level': {
                        'type': 'integer'
                    },
                    'ident': {
                        'type': 'keyword'
                    },
                    'name': {
                        'type': 'text',
                        'fields': {
                            'keyword': {
                                'type': 'keyword',
                            }
                        }
                    },
                    'parent_name': {
                        'type': 'text',
                        'fields': {
                            'keyword': {
                                'type': 'keyword',
                            }
                        }
                    },
                    'parent': {
                        'type': 'keyword'
                    }
                }
            }
        }
    }
    
    return index_handler.regenerate_index('shapes', mapping)
    
@shared_task(ignore_result=False, name='geosystem.index_shapes')
def index_shapes(body={}):
    skip = 0
    shapes_count = 0
    filters = {}
    loop = True

    shapes = list(mongodb.get_all_records(
        'shapes', filters, limit=100, skip=skip))

    if body == {}:
        index_handler.delete_all_documents('shapes')

    while len(shapes) > 0 and loop:
        for shape_ in shapes:
            document = {}
            shapes_count += 1
            document['geometry'] = shape_['geometry']
            document['properties'] = {}
            document['properties']['admin_level'] = shape_['properties']['admin_level']
            document['properties']['ident'] = shape_['properties']['ident']
            document['properties']['name'] = shape_['properties']['name']
            if 'parent' in shape_['properties']:
                document['properties']['parent'] = shape_['properties']['parent']
            if 'parent_name' in shape_['properties']:
                document['properties']['parent_name'] = shape_['properties']['parent_name']
            index_handler.index_document(ELASTIC_INDEX_PREFIX + '-shapes', str(shape_['_id']), document)

        skip += 100
        shapes = list(mongodb.get_all_records(
            'shapes', filters, limit=100, skip=skip))

    resp = _("Indexing finished for %(count)s resources", count=shapes_count)
    return resp
    

@cacheHandler.cache.cache(limit=5000)
def get_level(body):
    try:
        level = body.get('level', 0)
        bounds = body.get('bounds', None)
        threshold = float(body.get('area_threshold', 0))
        threshold = 4.0 if int(level) == 0 else threshold
        filters = {
            'properties.admin_level': int(level)
        }
        if 'parent' in body:
            filters['properties.parent'] = body['parent']
            
        if 'bounds' in body and bounds:
            bounds_width = abs(bounds['maxLng'] - bounds['minLng'])
            bounds_height = abs(bounds['maxLat'] - bounds['minLat'])
            bounds_area = bounds_width * bounds_height
            
            if bounds_area < 400 and bounds_area > 40:  # Adjust threshold as needed
                filters['properties.admin_level'] = {'$gte': int(level)}
                filters['properties.admin_level'] = {'$lt': int(level) + 2}
                threshold = 0.1
            elif bounds_area < 40:
                filters['properties.admin_level'] = 2
                threshold = 0.01
                
            filters['geometry'] = {
                '$geoIntersects': {
                    '$geometry': {
                        'type': 'Polygon',
                        'coordinates': [[
                            [bounds['minLng'], bounds['minLat']],
                            [bounds['maxLng'], bounds['minLat']],
                            [bounds['maxLng'], bounds['maxLat']],
                            [bounds['minLng'], bounds['maxLat']],
                            [bounds['minLng'], bounds['minLat']]
                        ]],
                        'crs': {
                            'type': "name",
                            'properties': { 'name': "EPSG:4326" }
                        }
                    }
                }
            }
            
        shapes = list(mongodb.get_all_records('shapes', filters, fields={'geometry': 1, 'properties.name': 1, 'properties.ident': 1}, sort=[('properties.admin_level', 1), ('properties.name', 1)]))
        
        filtered_shapes = []
        for s in shapes:
            geom = shape(s['geometry'])
            
            if geom.geom_type == 'Polygon':
                if geom.area < threshold:
                    continue
                valid_geom = geom
            elif geom.geom_type == 'MultiPolygon':
                valid_polygons = [poly for poly in geom.geoms if poly.area >= threshold]
                if not valid_polygons:
                    continue
                valid_geom = valid_polygons[0] if len(valid_polygons) == 1 else MultiPolygon(valid_polygons)
            else:
                continue
            
            s.pop('_id')
            s['centroid'] = mapping(valid_geom.centroid)
            if not bounds:
                geo = valid_geom.simplify(1 if int(level) == 0 else 0, preserve_topology=True)
            else:
                geo = valid_geom.simplify(0, preserve_topology=True)
            s['geometry'] = mapping(geo)
            filtered_shapes.append(s)
        
        shapes = filtered_shapes
        return shapes, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
@cacheHandler.cache.cache(limit=5000)
def get_level_info(body):
    try:
        level = body['level']
        filters = {
            'properties.admin_level': int(level)
        }
        if 'parent' in body:
            filters['properties.parent'] = body['parent']
        if 'ident' in body:
            filters['properties.ident'] = body['ident']
            
        shape = mongodb.get_record('shapes', filters, fields={'properties.name': 1, 'properties.ident': 1})
        if not shape:
            return {'msg': _('Forma no encontrada')}, 404
        shape.pop('_id')

        return shape, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
@cacheHandler.cache.cache(limit=5000)
def get_shape_centroid(ident, parent, level):
    try:
        if not ident:
            return None
            
        filters = {
            'properties.admin_level': level,
            'properties.ident': ident
        }
        if parent:
            filters['properties.parent'] = parent
            
        record = mongodb.get_record('shapes', filters, fields={'geometry': 1, 'properties.name': 1, 'properties.ident': 1})
        if not record:
            return None
            
        shape_ = shape(record['geometry'])
        
        if record['geometry']['type'] == 'MultiPolygon':
            centroids = []
            for polygon_coords in record['geometry']['coordinates']:
                polygon = {
                    'type': 'Polygon',
                    'coordinates': polygon_coords
                }
                poly_shape = shape(polygon)
                centroids.append(mapping(poly_shape.centroid))
            
            return centroids
        else:
            centroid = shape_.centroid
            return [mapping(centroid)]
    except Exception as e:
        raise Exception(f'Error al obtener el centroide de la forma {ident}')
    
@cacheHandler.cache.cache(limit=5000)
def get_shape_by_ident(ident, parent, level, type=None, retention=0.10):
    try:
        filters = {
            'properties.admin_level': level,
            'properties.type': type if type else {'$exists': False}
        }
        if ident:
            filters['properties.ident'] = ident
        if parent:
            filters['properties.parent'] = parent
            
        if not ident:
            records = list(mongodb.get_all_records('shapes', filters, fields={'geometry': 1, 'properties.name': 1, 'properties.ident': 1, 'type': 1}))
            for r in records:
                r.pop('_id', None)
                
            if records:
                fc = {'type': 'FeatureCollection', 'features': records}
                simplified = simplify_geojson(fc, retention)
                records = simplified.get('features', [])
                
            return records, 200
        else:
            record = mongodb.get_record('shapes', filters, fields={'geometry': 1, 'properties.name': 1, 'properties.ident': 1, 'type': 1})
            if not record:
                return {'msg': _('Forma no encontrada')}, 404
                
            record.pop('_id', None)
            
            fc = {'type': 'FeatureCollection', 'features': [record]}
            simplified = simplify_geojson(fc, retention)
            features = simplified.get('features', [])
            if features:
                record = features[0]
                
            return record, 200
    except Exception as e:
        return {'msg': str(e)}, 500