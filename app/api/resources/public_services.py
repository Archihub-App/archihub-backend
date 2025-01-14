from flask import jsonify, request
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from app.utils import HookHandler
from bson import json_util
import json
from bson.objectid import ObjectId
from app.api.resources.models import Resource
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.api.resources.models import ResourceUpdate
from app.api.types.services import is_hierarchical
from app.api.types.services import get_icon
from app.api.types.services import get_metadata
from app.api.types.services import get_parents as get_type_parents
from app.api.system.services import validate_text
from app.api.system.services import validate_text_array
from app.api.system.services import validate_text_regex
from app.api.system.services import get_value_by_path, set_value_in_dict
from app.api.system.services import get_default_visible_type
from app.api.system.services import validate_author_array
from app.api.system.services import validate_simple_date
from app.api.lists.services import get_option_by_id
from app.api.records.services import delete_parent
from app.api.records.services import update_parent
from app.api.records.services import update_record
from app.api.records.services import create as create_record
from app.api.system.services import get_access_rights
from app.utils.functions import get_resource_records_public, cache_type_roles, clear_cache
import os
from datetime import datetime
from dateutil import parser
from app.api.resources.services import get_total, get_accessRights, get_resource_type, get_children, get_children_cache

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()
hookHandler = HookHandler.HookHandler()
children_cache = {}

def update_cache():
    get_all.invalidate_all()
    get_tree.invalidate_all()
    get_resource.invalidate_all()
    get_resource_files.invalidate_all()
    get_resource_images.invalidate_all()

@cacheHandler.cache.cache(limit=5000)
def get_all(body):
    try:
        body = json.loads(body)
        post_types = body['post_type']

        for p in post_types:
            post_type_roles = cache_type_roles(p)
            if post_type_roles['viewRoles']:
                return {'msg': 'No tiene permisos para obtener los recursos'}, 401

        filters = {}
        limit = 20
        skip = 0
        filters['post_type'] = {"$in": post_types}

        if 'parents' in body:
            if body['parents']:
                filters['parents.id'] = body['parents']['id']

        if 'page' in body:
            skip = body['page'] * limit

        if 'files' in body:
            if body['files']:
                filters['filesObj'] = {'$exists': True, '$ne': []}

        filters['status'] = 'published'
        
        sort_direction = 1 if body.get('sortOrder', 'asc') == 'asc' else -1
        sortBy = body.get('sortBy', 'createdAt')
        
        # Obtener todos los recursos dado un tipo de contenido
        resources = list(mongodb.get_all_records(
            'resources', filters, limit=limit, skip=skip, fields={'metadata.firstLevel.title': 1, 'accessRights': 1, 'filesObj': 1, 'ident': 1}, sort=[(sortBy, sort_direction)]))
        
        # Obtener el total de recursos dado un tipo de contenido
        total = get_total(json.dumps(filters))
        # Para cada recurso, obtener el formulario asociado y quitar los campos _id
        for resource in resources:
            resource['id'] = str(resource['_id'])
            resource.pop('_id')
            if 'filesObj' in resource:
                resource['files'] = len(resource['filesObj'])
                resource.pop('filesObj')

            resource['accessRights'] = get_option_by_id(resource['accessRights'])
            if 'term' in resource['accessRights']:
                resource['accessRights'] = resource['accessRights']['term']
            else:
                resource['accessRights'] = None

        response = {
            'total': total,
            'resources': resources
        }
        # Retornar los recursos
        return response, 200
    except Exception as e:
        return {'msg': str(e)}, 500

def get_by_id(id):
    try:
        # Obtener los accessRights del recurso
        accessRights = get_accessRights(id)
        if accessRights:
            return {'msg': 'No tiene permisos para acceder al recurso'}, 401

        post_type = get_resource_type(id)
        post_type_roles = cache_type_roles(post_type)

        if post_type_roles['viewRoles']:
            return {'msg': 'No tiene permisos para obtener un recurso'}, 401

        resource = get_resource(id)

        # Retornar el recurso
        return jsonify(resource), 200
    except Exception as e:
        return {'msg': str(e)}, 500

@cacheHandler.cache.cache(limit=5000)
def get_resource(id):
    # Buscar el recurso en la base de datos
    resource = mongodb.get_record('resources', {'_id': ObjectId(id)})
    # Si el recurso no existe, retornar error
    if not resource:
        raise Exception('Recurso no existe')
    
    status = resource['status']
    if status == 'draft':
        raise Exception('No tiene permisos para ver este recurso')
        
    # Registrar el log
    resource['_id'] = str(resource['_id'])

    if 'parents' in resource:
        if resource['parents']:
            for r in resource['parents']:
                r_ = mongodb.get_record('resources', {'_id': ObjectId(r['id'])}, fields={'metadata.firstLevel.title': 1, 'post_type': 1})
                r['name'] = r_['metadata']['firstLevel']['title']
                r['icon'] = get_icon(r_['post_type'])

    resource['icon'] = get_icon(resource['post_type'])

    default_visible_type = get_default_visible_type()
    resource['children'] = mongodb.distinct('resources', 'post_type', {
                                            'parents.id': id, 'post_type': {'$in': default_visible_type['value']}})

    children = []
    for c in resource['children']:
        c_ = mongodb.get_record('post_types', {'slug': c})
        obj = {
            'post_type': c,
            'name': c_['name'],
            'icon': c_['icon'],
            'slug': c_['slug'],
        }
        children.append(obj)

    resource['children'] = children

    if 'filesObj' in resource:
        if len(resource['filesObj']) > 0:
            resource['children'] = [{
                'post_type': 'files',
                'name': 'Archivos asociados',
                'icon': 'archivo',
                'slug': 'files',
            }, *resource['children']]

            resource['files'] = len(resource['filesObj'])
        else:
            resource['files'] = None

    resource['fields'] = get_metadata(resource['post_type'])['fields']

    temp = []
    for f in resource['fields']:
        if f['type'] != 'file' and f['type'] != 'separator':
            accesRights = None
            if 'accessRights' in f:
                accesRights = f['accessRights']

            canView = True
            if accesRights:
                canView = False
            
            if not canView:
                set_value_in_dict(resource, f['destiny'], 'No tiene permisos para ver este campo')
                temp.append({
                    'label': f['label'],
                    'value': 'No tiene permisos para ver este campo',
                    'type': 'text'
                })
                continue

            if f['type'] == 'text' or f['type'] == 'text-area':
                value = get_value_by_path(resource, f['destiny'])

                if value:
                    temp.append({
                        'label': f['label'],
                        'value': value,
                        'type': f['type']
                    })
            elif f['type'] == 'select':
                value = get_value_by_path(resource, f['destiny'])
                value = get_option_by_id(value)
                if value and 'term' in value:
                    temp.append({
                        'label': value['term'],
                        'value': [value['term']],
                        'type': 'select'
                    })
            elif f['type'] == 'pattern':
                value = get_value_by_path(resource, f['destiny'])
                if value:
                    temp.append({
                        'label': f['label'],
                        'value': value,
                        'type': 'text'
                    })
            elif f['type'] == 'author':
                value = get_value_by_path(resource, f['destiny'])
                if value:
                    temp_ = []
                    for v in value:
                        # si v tiene el caracter | o ,
                        if '|' in v:
                            temp_.append(' '.join(v.split('|')))
                        elif ',' in v:
                            temp_.append(' '.join(v.split(',')))

                    temp.append({
                        'label': f['label'],
                        'value': temp_,
                        'type': 'author'
                    })
            elif f['type'] == 'select-multiple2':
                value = get_value_by_path(resource, f['destiny'])
                if value:
                    temp_ = []
                    for v in value:
                        v_ = get_option_by_id(v['id'])
                        temp_.append(v_['term'])

                    temp.append({
                        'label': f['label'],
                        'value': temp_,
                        'type': 'select'
                    })
            elif f['type'] == 'simple-date':
                value = get_value_by_path(resource, f['destiny'])
                if value:
                    if isinstance(value, datetime):
                        value = value.isoformat()
                        set_value_in_dict(resource, f['destiny'], value)

                    temp.append({
                        'label': f['label'],
                        'value': value,
                        'type': 'simple-date'
                    })
            elif f['type'] == 'relation':
                value = get_value_by_path(resource, f['destiny'])

                if value:
                    temp_ = []
                    for v in value:
                        r = mongodb.get_record('resources', {'_id': ObjectId(v['id'])}, fields={'metadata.firstLevel.title': 1})
                        temp_.append({
                            'id': v['id'],
                            'post_type': v['post_type'],
                            'name': r['metadata']['firstLevel']['title'],
                            'icon': get_icon(v['post_type'])
                        })

                    temp.append({
                        'label': f['label'],
                        'value': temp_,
                        'type': 'relation'
                    })
            

    resource['fields'] = temp
    resource['accessRights'] = get_option_by_id(resource['accessRights'])
    if 'term' in resource['accessRights']:
        resource['accessRights'] = str(resource['accessRights']['_id'])
    else:
        resource['accessRights'] = None

    if 'createdAt' in resource:
        resource['createdAt'] = resource['createdAt'].isoformat()

    return resource

@cacheHandler.cache.cache(limit=1000)
def get_resource_files(id, page, groupImages = False):
    try:
        resource = mongodb.get_record('resources', {'_id': ObjectId(id)})
        # check accessRights
        accessRights = get_accessRights(id)
        if accessRights:
            return {'msg': 'No tiene permisos para acceder al recurso'}, 401
        # Si el recurso no existe, retornar error
        if not resource:
            return {'msg': 'Recurso no existe'}, 404

        temp = []
        ids = []
        if 'filesObj' in resource:
            for r in resource['filesObj']:
                ids.append(r)

        r_ = get_resource_records_public(json.dumps(ids), page, groupImages=groupImages)
        for _ in r_:
            obj = {
                'id': str(_['_id']),
                'hash': _['hash'],
                'tag': _['tag'],
            }

            if 'displayName' in _: obj['displayName'] = _['displayName']
            else : obj['displayName'] = _['name']
            if 'accessRights' in _: obj['accessRights'] = _['accessRights']
            if 'processing' in _: obj['processing'] = _['processing']

            temp.append(obj)

        resp = {
            'data': temp,
            'total': len(r_)
        }
        # Retornar el recurso
        return resp, 200
    except Exception as e:
        return {'msg': str(e)}, 500

@cacheHandler.cache.cache(limit=5000)
def get_tree(root, available, post_type=None, page=0):
    try:
        list_available = available.split('|')

        fields = {'metadata.firstLevel.title': 1, 'post_type': 1, 'parent': 1}

        if root == 'all':
            if page is not None:
                resources = list(mongodb.get_all_records('resources', {
                             'post_type': {
                             "$in": list_available}, 'parent': None, 'status': 'published'}, sort=[('metadata.firstLevel.title', 1)], fields=fields, limit=10, skip=page * 10))
            else:
                resources = list(mongodb.get_all_records('resources', {
                             'post_type': {
                             "$in": list_available}, 'parent': None, 'status': 'published'}, sort=[('metadata.firstLevel.title', 1)], fields=fields))
        else:
            if page is not None:
                resources = list(mongodb.get_all_records('resources', {'post_type': {
                             "$in": list_available}, 'parent.id': root, 'status': 'published'}, sort=[('metadata.firstLevel.title', 1)], fields=fields, limit=10, skip=page * 10))
            else:
                resources = list(mongodb.get_all_records('resources', {'post_type': {
                             "$in": list_available}, 'parent.id': root, 'status': 'published'}, sort=[('metadata.firstLevel.title', 1)], fields=fields))
                
        resources = [{'name': re['metadata']['firstLevel']['title'], 'post_type': re['post_type'], 'id': str(
            re['_id'])} for re in resources]

        for resource in resources:
            resource['children'] = get_children(resource['id'], available, False, post_type)
            resource['icon'] = get_icon(resource['post_type'])
            
        # Retornar los recursos y los padres
        return resources, 200
    except Exception as e:
        return {'msg': str(e)}, 500

@cacheHandler.cache.cache()
def get_resource_images(id):
    resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'filesObj': 1})

    if not resource:
        return {'msg': 'Recurso no existe'}, 404

    ids = []
    if 'filesObj' in resource:
        for r in resource['filesObj']:
            ids.append(r['id'])

    img = mongodb.count('records', {'_id': {'$in': [ObjectId(id) for id in ids]}, 'processing.fileProcessing.type': 'image'})
    if img == 0:
        return {'msg': 'No hay imágenes asociadas al recurso'}, 404
    
    resp = {
        'pages': img
    }

    return resp, 200