from celery import shared_task
from app.utils import DatabaseHandler
from app.utils import IndexHandler
import os
from bson.objectid import ObjectId
from app.utils.index.spanish_settings import settings as spanish_settings
from app.api.types.services import get_by_slug, get_metadata
from flask_babel import _
from html.parser import HTMLParser
import re

index_handler = IndexHandler.IndexHandler()
mongodb = DatabaseHandler.DatabaseHandler()
ELASTIC_INDEX_PREFIX = os.environ.get('ELASTIC_INDEX_PREFIX', '')

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_data(self):
        return ''.join(self._parts)


def strip_html(text):
    if text is None:
        return None
    stripper = _HTMLStripper()
    stripper.feed(text)
    stripper.close()
    cleaned = stripper.get_data()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    cleaned = re.sub(r'\s+([,.;:!?])', r'\1', cleaned)
    cleaned = re.sub(r'([,.;:!?])([^\s])', r'\1 \2', cleaned)
    return cleaned.strip()

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
    return index_handler.regenerate_index('resources', mapping)

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
            try:
                document = {}
                resouces_count += 1
                post_type = resource['post_type']
                post_type_ = get_by_slug(post_type)
                fields = get_metadata(post_type)['fields']
                isArticle = post_type_ and 'isArticle' in post_type_ and post_type_['isArticle']
                
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
                                    
                    if f['type'] == 'select-multiple2':
                        destiny = f['destiny']
                        if destiny != '':
                            value = get_value_by_path(resource, destiny)
                            if value != None and isinstance(value, list):
                                value = [str(v['term']) for v in value if 'term' in v]
                                value = list(set(value))
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
                                    if isinstance(v, dict):
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
                document['article'] = None
                
                if isArticle:
                    articleBody = resource['articleBody'] if 'articleBody' in resource else []
                    for p in articleBody:
                        if 'type' in p and p['type'] == 'paragraph':
                            if 'content' in p:
                                if document['article'] is None:
                                    document['article'] = ''
                                
                                content = strip_html(p['content'])
                                if document['article'] == '':
                                    document['article'] += content
                                else:
                                    document['article'] += ' ' + content
                
                if 'createdAt' in resource:
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
                
                records_ids = []
                records_labels_map = {}
                if 'filesObj' in resource:
                    records_ids = [r['id'] for r in resource['filesObj']]
                    records_labels_map = {r['id']: r.get('tag') for r in resource['filesObj'] if 'id' in r}
                document['records'] = []
                records_ids = [ObjectId(r) for r in records_ids]
                if records_ids:
                    records_list = list(mongodb.get_all_records(
                        'records', {'_id': {'$in': records_ids}}, {'_id': 1, 'processing.fileProcessing.type': 1}))
                    records_map = {record['_id']: record for record in records_list}
                    records = [records_map[id] for id in records_ids if id in records_map]
                else:
                    records = []
                    
                records = [
                    {
                        'id': str(record['_id']),
                        'type': record['processing']['fileProcessing']['type'],
                        'tag': records_labels_map.get(str(record['_id']))
                    }
                    for record in records
                    if 'processing' in record and 'fileProcessing' in record['processing']
                ]
                document['records'] = records

                if 'accessRights' in resource:
                    if resource['accessRights']:
                        document['accessRights'] = resource['accessRights']

                r = index_handler.index_document(
                    ELASTIC_INDEX_PREFIX + '-resources', str(resource['_id']), document)
                if r.status_code != 201 and r.status_code != 200:
                    print(r.text)
                    raise Exception(
                        'Error al indexar el recurso ' + str(resource['_id']))
            except Exception as e:
                continue

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