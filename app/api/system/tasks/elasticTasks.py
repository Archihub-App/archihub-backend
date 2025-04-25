from celery import shared_task
from app.utils import DatabaseHandler
from app.utils import IndexHandler
import os
from bson.objectid import ObjectId
from app.utils.index.spanish_settings import settings as spanish_settings
from app.api.types.services import get_metadata
from flask_babel import _

index_handler = IndexHandler.IndexHandler()
mongodb = DatabaseHandler.DatabaseHandler()
ELASTIC_INDEX_PREFIX = os.environ.get('ELASTIC_INDEX_PREFIX', '')

def get_value_by_path(dict, path):
    try:
        keys = path.split('.')
        value = dict
        for key in keys:
            if key in value:
                value = value.get(key)
            else:
                value = None
                break

        return value

    except Exception as e:
        raise Exception(f'Error al obtener el valor del campo {key}')
    
def change_value(body, path, value):
    try:
        keys = path.split('.')
        temp = body
        for key in keys:
            if key not in temp:
                temp[key] = {}
            if key == keys[-1]:
                temp[key] = value
            else:
                temp = temp[key]
        return body
    except Exception as e:
        raise Exception(f'Error al cambiar el valor del campo {key}')

@shared_task(ignore_result=False, name='system.regenerate_index')
def regenerate_index_task(mapping, user):
    return index_handler.regenerate_index(mapping, 'resources')

@shared_task(ignore_result=False, name='system.index_resources')
def index_resources_task(body={}):
    skip = 0
    resouces_count = 0
    filters = {}
    loop = True
    if '_id' in body:
        filters['_id'] = ObjectId(body['_id'])

    resources = list(mongodb.get_all_records(
        'resources', filters, limit=1000, skip=skip))

    if body == {}:
        index_handler.delete_all_documents(ELASTIC_INDEX_PREFIX + '-resources')

    while len(resources) > 0 and loop:
        for resource in resources:
            document = {}
            resouces_count += 1
            post_type = resource['post_type']
            fields = get_metadata(post_type)['fields']
            for f in fields:
                if f['type'] != 'file' and f['type'] != 'simple-date' and f['type'] != 'repeater':
                    destiny = f['destiny']
                    if destiny != '':
                        value = get_value_by_path(resource, destiny)
                        if value != None:
                            document = change_value(
                                document, f['destiny'], value)
                elif f['type'] == 'simple-date':
                    destiny = f['destiny']
                    if destiny != '':
                        value = get_value_by_path(resource, destiny)
                        if value != None:
                            import datetime
                            if isinstance(value, datetime.datetime):
                                value = value.strftime('%Y-%m-%dT%H:%M:%S')
                                change_value(document, f['destiny'], value)
                if f['type'] == 'repeater':
                    value = get_value_by_path(resource, f['destiny'])
                    if value:
                        for v in value:
                            for s in f['subfields']:
                                if s['type'] == 'simple-date':
                                    date = get_value_by_path(v, s['destiny'])
                                    if date:
                                        date = date.strftime('%Y-%m-%dT%H:%M:%S')
                                        change_value(v, s['destiny'], date)
                if f['type'] == 'location':
                    value = get_value_by_path(document, f['destiny'])
                    temp = []
                    if value:
                        for v in value:
                            if 'coordinates' in v:
                                coordinates = v['coordinates']
                                if coordinates:
                                    if len(coordinates) == 2:
                                        newObj = {
                                            'type': 'Point',
                                            'coordinates': [coordinates[0], coordinates[1]]
                                        }
                                        temp.append(newObj)
                                    else:
                                        raise Exception(
                                            'Error al indexar el recurso ' + str(resource['_id']))
                            else:
                                for i in range(2, -1, -1):
                                    if v['level_' + str(i)]:
                                        level = v['level_' + str(i)]['ident']
                                        if level:
                                            if i == 0:
                                                parent = None
                                            else:
                                                parent = v['level_' + str(i - 1)]['ident']
                                            from app.api.geosystem.services import get_shape_centroid
                                            centroid = get_shape_centroid(level, parent, i)
                                            if centroid:
                                                temp = temp + centroid
                                                break
                                              
                        change_value(document, f['destiny'], temp)
                                            
                                    

            document['post_type'] = post_type
            
            if 'createdBy' in resource:
                created_at = resource['createdAt']
                created_at = created_at.strftime('%Y-%m-%dT%H:%M:%S')
                document['createdAt'] = created_at
            
            if 'parents' in resource:
                document['parents'] = resource['parents']
            if 'parent' in resource:
                document['parent'] = resource['parent']
            if 'ident' in resource:
                document['ident'] = resource['ident']
            if 'status' not in resource:
                continue
            document['status'] = resource['status']
            document['accessRights'] = 'public'
            document['files'] = len(
                resource['filesObj']) if 'filesObj' in resource else 0

            if 'accessRights' in resource:
                if resource['accessRights']:
                    document['accessRights'] = resource['accessRights']

            r = index_handler.index_document(
                ELASTIC_INDEX_PREFIX + '-resources', str(resource['_id']), document)
            if r.status_code != 201 and r.status_code != 200:
                print(r.text)
                raise Exception(
                    'Error al indexar el recurso ' + str(resource['_id']))

        if len(resources) < 1000:
            loop = False

        skip += 1000
        resources = list(mongodb.get_all_records(
            'resources', {}, limit=1000, skip=skip))

    resp = _("Indexing finished for %(count)s resources", count=resouces_count)
    return resp

@shared_task(ignore_result=False, name='system.index_resources_delete')
def index_resources_delete_task(body={}):
    r = index_handler.delete_document(
        ELASTIC_INDEX_PREFIX + '-resources', body['_id'])
    if r['result'] != 'deleted':
        raise Exception('Error al indexar el recurso ' + str(body['_id']))

    resp = _('Resource %(id)s deleted from index', id=body['_id'])
    return resp