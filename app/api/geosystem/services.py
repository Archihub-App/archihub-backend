from app.utils import CacheHandler
from app.utils import DatabaseHandler
from app.api.geosystem.models import Polygon
from app.api.geosystem.models import PolygonUpdate
import os
import json
from shapely.geometry import shape, mapping
from flask_babel import _

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

def update_cache():
    get_level.invalidate_all()
    get_level_info.invalidate_all()

def upload_shapes():
    try:
        print('Uploading shapes')
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
                    features = data['features']

                    mongodb.delete_records('shapes', {'properties.admin_level': level})
                    for feature in features:
                        feature['properties']['admin_level'] = level

                        if 'ident' not in feature['properties']:
                            raise Exception('Ident not found in properties')
                        if 'name' not in feature['properties']:	
                            raise Exception('Name not found in properties')
                        
                        feature['properties']['name'] = feature['properties']['name'].capitalize()

                        if level > 0:
                            shape_intersect = list(mongodb.get_all_records('shapes',
                                                                      {'properties.admin_level': level - 1, 'geometry': {'$geoIntersects': {'$geometry': feature['geometry']}}},
                                                                        fields={'_id': 1, 'geometry': 1, 'properties.ident': 1, 'properties.name': 1}
                                                                      ))

                            for s in shape_intersect:
                                candidate = shape(s['geometry'])
                                feature_shape = shape(feature['geometry'])
                                centroid = feature_shape.centroid
                                if candidate.contains(centroid):
                                    feature['properties']['parent'] = s['properties']['ident']
                                    feature['properties']['parent_name'] = s['properties']['name']
                                    mongodb.insert_record('shapes', Polygon(**feature))
                                else:
                                    if feature['geometry']['type'] == 'MultiPolygon':
                                        if len(shape_intersect) == 1:
                                            feature['properties']['parent'] = s['properties']['ident']
                                            feature['properties']['parent_name'] = s['properties']['name']
                                            mongodb.insert_record('shapes', Polygon(**feature))
                        elif level == 0:
                            mongodb.insert_record('shapes', Polygon(**feature))

                        get_level.invalidate_all()
                        

        return {'msg': _('Shapes uploaded successfully')}, 200
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500

@cacheHandler.cache.cache(limit=5000)
def get_level(body):
    try:
        level = body['level']
        filters = {
            'properties.admin_level': int(level)
        }
        if 'parent' in body:
            filters['properties.parent'] = body['parent']
            
        shapes = list(mongodb.get_all_records('shapes', filters, fields={'geometry': 1, 'properties.name': 1, 'properties.ident': 1}, sort=[('properties.name', 1)]))

        for s in shapes:
            shape_ = shape(s['geometry'])
            s.pop('_id')
            s['centroid'] = mapping(shape_.centroid)
            geo = shape_.simplify(.85, preserve_topology=True)
            s['geometry'] = mapping(geo)

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
        shape.pop('_id')

        return shape, 200
    except Exception as e:
        return {'msg': str(e)}, 500