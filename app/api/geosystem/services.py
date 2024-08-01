from app.utils import CacheHandler
from app.utils import DatabaseHandler
from app.api.geosystem.models import Polygon
from app.api.geosystem.models import PolygonUpdate
import os
import json
from shapely.geometry import shape

mongodb = DatabaseHandler.DatabaseHandler()

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
            print(level, admin_folder_path)
            for shape_ in os.listdir(admin_folder_path):
                print(shape_)
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
                                # get feature shape centroid
                                centroid = feature_shape.centroid
                                # check if centroid is inside the candidate shape
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
                        

        return {'msg': 'Shapes uploaded'}, 200
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500
