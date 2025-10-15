from flask import jsonify, request, send_file
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
from app.api.users.services import has_right, has_role
from app.utils.functions import get_resource_records, cache_type_roles, clear_cache
from app.api.types.services import get_by_slug
import os
from datetime import datetime
from dateutil import parser
import numbers
from flask_babel import _

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()
hookHandler = HookHandler.HookHandler()
children_cache = {}

ORIGINAL_FILES_PATH = os.environ.get('ORIGINAL_FILES_PATH', '')
WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')

# function que recibe un body y una ruta tipo string y cambia el valor en la ruta dejando el resto igual y retornando el body con el valor cambiado
def change_value(body, route, value):
    route = route.split('.')
    temp = body
    for i in range(len(route)):
        if i == len(route) - 1:
            temp[route[i]] = value
        else:
            temp = temp[route[i]]
    return body

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para obtener todos los recursos dado un tipo de contenido
@cacheHandler.cache.cache(limit=5000)
def get_all(body, user):
    try:
        body = json.loads(body)
        activeColumns = body.get('activeColumns', [])
        post_types = body['post_type']
        body.pop('post_type')
        for p in post_types:
            post_type_roles = cache_type_roles(p)
            if post_type_roles['viewRoles']:
                canView = False
                for r in post_type_roles['viewRoles']:
                    if has_role(user, r) or has_role(user, 'admin'):
                        canView = True
                        break
                if not canView:
                    return {'msg': _('You don\'t have the required authorization')}, 401

        filters = {}
        limit = 20
        skip = 0
        filters['post_type'] = {"$in": post_types}
        metadata_fields = []
        for post_type in post_types:
            from app.api.types.services import get_metadata
            metadata = get_metadata(post_type)
            if metadata and 'fields' in metadata:
                for field in metadata['fields']:
                    if field['destiny'] not in [m_field['destiny'] for m_field in metadata_fields] and field['destiny'] in activeColumns:
                        metadata_fields.append({
                            'destiny': field['destiny'],
                            'type': field['type'],
                        })

        if 'parents' in body:
            if body['parents']:
                filters['parents.id'] = body['parents']['id']

        if 'status' not in body:
            body['status'] = 'published'

        if 'page' in body:
            skip = body['page'] * limit

        if 'files' in body:
            if body['files']:
                filters['filesObj'] = {'$exists': True, '$ne': []}

        filters['status'] = body['status']

        if filters['status'] == 'draft':
            filters.pop('status')
            filters_ = {'$or': [{'status': 'draft', **filters}, {'status': 'created', **filters}, {'status': 'updated', **filters}]}
            if not has_role(user, 'publisher') or not has_role(user, 'admin'):
                for o in filters_['$or']:
                    o['createdBy'] = user
            filters = filters_

        # Obtener todos los recursos dado un tipo de contenido
        sort_direction = 1 if body.get('sortOrder', 'asc') == 'asc' else -1
        sortBy = body.get('sortBy', 'createdAt')
        activeColumns = [col['destiny'] for col in activeColumns if col['destiny'] != '' and col['destiny'] != 'createdAt' and col['destiny'] != 'ident' and col['destiny'] != 'files' and col['destiny'] != 'accessRights']
        
        fields = {'accessRights': 1, 'filesObj': 1, 'ident': 1, 'post_type': 1, 'createdAt': 1}
        if activeColumns:
            for col in activeColumns:
                fields[col] = 1
                metadata_field = next((f for f in metadata_fields if f['destiny'] == col), None)
                if metadata_field and metadata_field['type'] != 'text':
                    filters[col] = {'$exists': True, '$ne': None}
                    
        resources = list(mongodb.get_all_records(
            'resources', filters, limit=limit, skip=skip, fields=fields, sort=[(sortBy, sort_direction)]))
        # Obtener el total de recursos dado un tipo de contenido
        total = get_total(json.dumps(filters))
        
        def convert_date_field(resource: dict, field_path: str):
            keys = field_path.split('.')
            current = resource
            for k in keys[:-1]:
                if isinstance(current, dict) and k in current:
                    current = current[k]
                else:
                    return
            last_key = keys[-1]
            if isinstance(current, dict) and last_key in current:
                value = current[last_key]
                if isinstance(value, datetime):
                    current[last_key] = value.isoformat() 
                    
        
        for resource in resources:
            resource['id'] = str(resource['_id'])
            resource.pop('_id')
            if 'filesObj' in resource:
                resource['files'] = len(resource['filesObj'])
                resource.pop('filesObj')

            resource['accessRights'] = get_option_by_id(resource['accessRights'])
            if resource['accessRights'] and 'term' in resource['accessRights']:
                resource['accessRights'] = resource['accessRights']['term']
            else:
                resource['accessRights'] = None
            
            for key in fields:
                convert_date_field(resource, key)
        

        response = {
            'total': total,
            'resources': resources
        }
        # Retornar los recursos
        return response, 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para crear un recurso
def create(body, user, files, updateCache = True):
    try:
        # si el body tiene parents, verificar que el recurso sea jerarquico
        body = validate_parent(body)
        
        # Si el body no tiene metadata, retornar error
        if 'metadata' not in body:
            return {'msg': _('The metadata is required')}, 400


        status = body['status']
        if status == 'published':
            if not has_role(user, 'publisher') and not has_role(user, 'admin'):
                return {'msg': _('You don\'t have the required authorization')}, 401
            
        if not status:
            status = 'draft'

        body['createdBy'] = user
            
        # Obtener los metadatos en función del tipo de contenido
        metadata = get_metadata(body['post_type'])
        
        errors = {}
        # Validar los campos de la metadata
        body_tmp = hookHandler.call('resource_pre_create', body)
        if body_tmp:
            body = body_tmp
        body = validate_fields(body, metadata, errors)

        update_relations_children(body, metadata['fields'], True)

        if 'ident' not in body:
            body['ident'] = 'ident'
        
        if errors:
            print(errors)
            return {'msg': 'Error al validar los campos', 'errors': errors}, 400

        array_files = False
        temp_files = []
        temp_files_obj = body['filesIds']
        
        if 'files' in body:
            if len(body['files']) > 0:
                if 'filename' in body['files'][0]:
                    array_files = True
                    temp_files = body['files']
        
        body['files'] = []
        del body['filesIds']
        body['filesObj'] = []
        body['createdAt'] = datetime.now()
        body['updatedAt'] = datetime.now()
        body['updatedBy'] = user
        
        # Crear instancia de Resource con el body del request
        resource = Resource(**body)

        # Insertar el recurso en la base de datos
        new_resource = mongodb.insert_record('resources', resource)
        body['_id'] = str(new_resource.inserted_id)
        # Registrar el log
        register_log(user, log_actions['resource_create'], {'resource': body})

        # limpiar la cache
        if updateCache:
            update_cache()
        
        hookHandler.call('resource_create', body)

        if files:
        # crear el record
            try:
                # si files es una lista
                if not array_files:
                    records = create_record(str(body['_id']), user, files, filesTags = temp_files_obj)
                else:
                    records = create_record(str(body['_id']), user, temp_files, upload = False, filesTags = temp_files_obj)
            except Exception as e:
                print(str(e))
                return {'msg': str(e)}, 500

            update = {
                'filesObj': records,
                'updatedAt': datetime.now(),
                'updatedBy': user,
                'post_type': body['post_type'],
                '_id': body['_id'],
                'metadata': body['metadata'],
            }

            update_ = ResourceUpdate(**update)

            mongodb.update_record(
                'resources', {'_id': ObjectId(body['_id'])}, update_)
            
            # limpiar la cache
            if updateCache:
                update_cache()
            
            hookHandler.call('resource_files_create', update)

        # Retornar el resultado
        resp = {'msg': _('Resource created successfully'), 'id': str(new_resource.inserted_id), 'post_type': body['post_type']}
        return resp, 201
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500

def update_by_id(id, body, user, files, updateCache = True):
    try:
        body = validate_parent(body, True)
        has_new_parent = has_changed_parent(id, body)

        # # Obtener los metadatos en función del tipo de contenido
        metadata = get_metadata(body['post_type'])
        body_tmp = hookHandler.call('resource_pre_update', body)
        if body_tmp:
            body = body_tmp
        
        errors = {}

        # Validar los campos de la metadata
        body = validate_fields(body, metadata, errors)

        if errors:
            return {'msg': _('Error validating fields'), 'errors': errors}, 400
        
        resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'filesObj': 1, 'createdBy': 1})
        
        if resource and 'createdBy' in resource and resource['createdBy'] != user and not has_role(user, 'admin') and not has_role(user, 'super_editor'):
            return {'msg': _('You don\'t have the required authorization')}, 401

        validate_files([*body['filesIds'], *resource['filesObj']], metadata, errors)

        if errors:
            return {'msg': _('Error validating files'), 'errors': errors}, 400

        status = body['status']
        if status == 'published' and user:
            if not has_role(user, 'publisher') and not has_role(user, 'admin'):
                return {'msg': _('You don\'t have the required authorization')}, 401
        if not status:
            status = 'draft'

        temp = []
        temp_files_obj = body['filesIds']

        if 'filesObj' in resource:
            for f in resource['filesObj']:
                temp.append(f)

        body['filesObj'] = temp
        del body['filesIds']
        body['updatedAt'] = datetime.now()
        body['updatedBy'] = user if user else 'system'

        # Crear instancia de ResourceUpdate con el body del request
        try:
            resource = ResourceUpdate(**body)
        except Exception as e:
            print("Validation error details:", e.errors() if hasattr(e, 'errors') else str(e))

        # Actualizar el recurso en la base de datos
        updated_resource = mongodb.update_record(
            'resources', {'_id': ObjectId(id)}, resource)

        if has_new_parent:
            update_parents(id, body['post_type'], user)
            update_records_parents(id, user)

        try:
            records = create_record(id, user, files, filesTags = temp_files_obj)
        except Exception as e:
            return {'msg': str(e)}, 500

        delete_records(body['deletedFiles'], id, user)
        update_records(body['updatedFiles'], user)
        
        body['filesObj'] = [f for f in body['filesObj'] if f['id'] not in body['deletedFiles']]

        update = {
            'post_type': body['post_type'],
            'metadata': body['metadata'],
            '_id': body['_id'],
            'filesObj': [*body['filesObj'], *records],
            'updatedAt': datetime.now(),
            'updatedBy': user if user else 'system'
        }
        

        seen = set()
        new_list = []
        for d in update['filesObj']:
            t = tuple(d.items())
            if t not in seen:
                seen.add(t)
                new_list.append(d)
        update['filesObj'] = new_list

        try:
            update_ = ResourceUpdate(**update)
        except Exception as e:
            print("Validation error details:", e.errors() if hasattr(e, 'errors') else str(e))

        mongodb.update_record(
            'resources', {'_id': ObjectId(body['_id'])}, update_)
        
        hookHandler.call('resource_update', update)

        # Registrar el log
        register_log(user, log_actions['resource_update'], {'resource': body})
        # limpiar la cache
        if updateCache:
            update_cache()
        # Retornar el resultado
        return {'msg': _('Resource updated successfully')}, 200
    except Exception as e:
        return {'msg': str(e)}, 500


# Funcion para actualizar los recursos relacionados si el post_type es igual al del padre
def update_relations_children(body, metadata, new = False):
    for f in metadata:
        if f['type'] == 'relation':
            if f['relation_type'] == body['post_type'] and get_value_by_path(body, f['destiny']):
                if not new:
                    current = mongodb.get_record('resources', {'_id': ObjectId(body['_id'])})
                    current_children = get_value_by_path(current, f['destiny'])
                    
                    if current_children:
                        current_children = [c['id'] for c in current_children]
                    else:
                        current_children = []
                else:
                    current_children = []

                # comparar los children actuales con los nuevos
                new_children = get_value_by_path(body, f['destiny'])
                new_children = [c['id'] for c in new_children]

                to_delete = [item for item in current_children if item not in new_children]
                to_add = [item for item in new_children if item not in current_children]

                for d in to_delete:
                    child_field_body = mongodb.get_record('resources', {'_id': ObjectId(d)})
                    child_field = get_value_by_path(child_field_body, f['destiny'])
                    if not child_field:
                        child_field = []

                    temp = []
                    require_update = False
                    for c in child_field:
                        if c['id'] != body['_id']:
                            temp.append(c)
                        else:
                            require_update = True
                    
                    if not require_update:
                        continue

                    update = {**child_field_body}
                    from app.api.system.services import set_value_in_dict
                    set_value_in_dict(update, f['destiny'], temp)

                    update_ = ResourceUpdate(**update)

                    mongodb.update_record('resources', {'_id': ObjectId(d)}, update_)

                for a in to_add:
                    child_field_body = mongodb.get_record('resources', {'_id': ObjectId(a)})
                    child_field = get_value_by_path(child_field_body, f['destiny'])
                    if not child_field:
                        child_field = []
                    
                    temp = []
                    require_update = True
                    for c in child_field:
                        temp.append(c)
                        if c['id'] == body['_id']:
                            require_update = False

                    if not require_update:
                        continue                            

                    temp.append({
                        'id': body['_id'],
                        'post_type': body['post_type']
                    })

                    update = {**child_field_body}
                    from app.api.system.services import set_value_in_dict
                    set_value_in_dict(update, f['destiny'], temp)

                    update_ = ResourceUpdate(**update)

                    mongodb.update_record('resources', {'_id': ObjectId(a)}, update_)

# Funcion para validar el padre de un recurso
def validate_parent(body, update = False):
    if 'parent' in body and body['parent']:
        parent = body['parent']
        all_ancestors = []

        if len(parent) == 0:
            body['parent'] = []
            body['parents'] = []
            return body
        
        for p in parent:
            if 'id' not in p:
                body['parent'] = []
                body['parents'] = []
                return body
            all_ancestors.extend(get_parents(p['id']))
        
        ancestor_ids = {ancestor['id'] for ancestor in all_ancestors}
        final_direct_parents = [p for p in parent if p['id'] not in ancestor_ids]
        
        
        parents = []
        for p in final_direct_parents:
            if update:
                if p['id'] == body['_id']:
                    raise Exception(_('The resource cannot have itself as parent'))

            if 'post_type' not in p:
                parent_temp = mongodb.get_record('resources', {'_id': ObjectId(p['id'])}, fields={'post_type': 1})
                if not parent_temp:
                    raise Exception(_('The parent resource does not exist'))
                p['post_type'] = parent_temp['post_type']

            if p['post_type'] == body['post_type']:
                hierarchical = is_hierarchical(body['post_type'])
                if not hierarchical[0]:
                    raise Exception(_('The resource isn\'t hierarchical'))
                elif not has_parent_postType(body['post_type'], p['post_type']) and not hierarchical[0]:
                    raise Exception(_('The resource post type is not allowed to have a parent of this type'))
                
            parents.append(p)
            parents = [*parents, *get_parents(p['id'])]
        
        unique_parents = []
        seen_ids = set()
        for p in parents:
            if p['id'] not in seen_ids:
                unique_parents.append(p)
                seen_ids.add(p['id'])
            else:
                # find the parent in unique_parents and add the parentOf
                for up in unique_parents:
                    if up['id'] == p['id'] and 'parentOf' in p and 'parentOf' in up:
                        combined_parent_of = up['parentOf'] + p['parentOf']
                        
                        def flatten(items):
                            for item in items:
                                if isinstance(item, list):
                                    yield from flatten(item)
                                else:
                                    yield item
                        
                        flattened_parent_of = list(flatten(combined_parent_of))
                        up['parentOf'] = list(set(flattened_parent_of))
                        break
                

        body['parents'] = unique_parents
        body['parent'] = final_direct_parents
        
        print(body['parents'])
        return body
    
    else:
        body['parent'] = []
        body['parents'] = []
        return body

# Funcion para validar los campos de la metadata
def validate_fields(body, metadata, errors):
    for field in metadata['fields']:
        try:
            bodyTmp = hookHandler.call('validate_field', body, field, metadata, errors)
            if bodyTmp:
                body = bodyTmp
                
            if field['type'] != 'file' and field['type'] != 'separator':
                if field['destiny'] != 'ident':
                    if field['destiny'] == 'metadata.firstLevel.title':
                        value = get_value_by_path(body, field['destiny'])
                        if not value or value == '':
                            if field['required'] and body['status'] == 'published':
                                errors[field['destiny']] = _(u'The field {label} is required', label=field['label'])
                            else:
                                body = change_value(body, field['destiny'], 'Sin título')
                    if field['type'] == 'location':
                        exists = get_value_by_path(body, field['destiny'])
                        hasCondition = int(field['conditionField']) if 'conditionField' in field else False
                        conditionField = metadata['fields'][hasCondition] if hasCondition else False
                        
                        if exists:
                            if not isinstance(get_value_by_path(body, field['destiny']), list):
                                errors[field['destiny']
                                    ] = _(u'The field {label} must be a list', label=field['label'])
                            else:
                                for l in get_value_by_path(body, field['destiny']):
                                    if not isinstance(l, dict):
                                        errors[field['destiny']
                                            ] = _(u'The field {label} must be a list of dicts', label=field['label'])
                                    
                        
                        if hasCondition:
                            if conditionField['type'] == 'checkbox':
                                conditionFieldVal = get_value_by_path(body, conditionField['destiny'])
                                if not conditionFieldVal:
                                    body = change_value(body, field['destiny'], None)
                    if field['type'] == 'text':
                        exists = get_value_by_path(body, field['destiny'])
                        hasCondition = int(field['conditionField']) if 'conditionField' in field else False
                        conditionField = metadata['fields'][hasCondition] if hasCondition else False
                        if exists:
                            validate_text(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']
                                   ] = _(u'The field {label} is required', label=field['label'])
                            
                        if hasCondition:
                            if conditionField['type'] == 'checkbox':
                                conditionFieldVal = get_value_by_path(body, conditionField['destiny'])
                                if not conditionFieldVal:
                                    body = change_value(body, field['destiny'], '')
                                    
                    elif field['type'] == 'text-area':
                        exists = get_value_by_path(body, field['destiny'])
                        hasCondition = int(field['conditionField']) if 'conditionField' in field else False
                        conditionField = metadata['fields'][hasCondition] if hasCondition else False
                        if exists:
                            validate_text(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']
                                   ] = _(u'The field {label} is required', label=field['label'])
                            
                        if hasCondition:
                            if conditionField['type'] == 'checkbox':
                                conditionFieldVal = get_value_by_path(body, conditionField['destiny'])
                                if not conditionFieldVal:
                                    body = change_value(body, field['destiny'], '')
                    elif field['type'] == 'select':
                        exists = get_value_by_path(body, field['destiny'])
                        hasCondition = int(field['conditionField']) if 'conditionField' in field else False
                        conditionField = metadata['fields'][hasCondition] if hasCondition else False
                        if exists:
                            validate_text(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required'] and body['status'] == 'published' and field['destiny'] != 'accessRights':
                            errors[field['destiny']
                                   ] = _(u'The field {label} is required', label=field['label'])
                        
                        if hasCondition:
                            if conditionField['type'] == 'checkbox':
                                conditionFieldVal = get_value_by_path(body, conditionField['destiny'])
                                if not conditionFieldVal:
                                    body = change_value(body, field['destiny'], None)
                    elif field['type'] == 'number':
                        exists = get_value_by_path(body, field['destiny'])
                        hasCondition = int(field['conditionField']) if 'conditionField' in field else False
                        conditionField = metadata['fields'][hasCondition] if hasCondition else False
                        if exists:
                            if not isinstance(get_value_by_path(body, field['destiny']), numbers.Number):
                                errors[field['destiny']
                                    ] = _(u'The field {label} must be a number', label=field['label'])
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']
                                   ] = _(u'The field {label} is required', label=field['label'])
                            
                        if hasCondition:
                            if conditionField['type'] == 'checkbox':
                                conditionFieldVal = get_value_by_path(body, conditionField['destiny'])
                                if not conditionFieldVal:
                                    body = change_value(body, field['destiny'], None)
                    elif field['type'] == 'checkbox':
                        exists = get_value_by_path(body, field['destiny'])
                        hasCondition = int(field['conditionField']) if 'conditionField' in field else False
                        conditionField = metadata['fields'][hasCondition] if hasCondition else False
                        if exists:
                            if not isinstance(get_value_by_path(body, field['destiny']), bool):
                                errors[field['destiny']
                                    ] = _(u'The field {label} must be a boolean', label=field['label'])
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']
                                   ] = _(u'The field {label} is required', label=field['label'])
                            
                        if hasCondition:
                            if conditionField['type'] == 'checkbox':
                                conditionFieldVal = get_value_by_path(body, conditionField['destiny'])
                                if not conditionFieldVal:
                                    body = change_value(body, field['destiny'], False)
                    elif field['type'] == 'select-multiple2':
                        exists = get_value_by_path(body, field['destiny'])
                        hasCondition = int(field['conditionField']) if 'conditionField' in field else False
                        conditionField = metadata['fields'][hasCondition] if hasCondition else False
                        if exists:
                            validate_text_array(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']
                                   ] = _(u'The field {label} is required', label=field['label'])
                            
                        if hasCondition:
                            if conditionField['type'] == 'checkbox':
                                conditionFieldVal = get_value_by_path(body, conditionField['destiny'])
                                if not conditionFieldVal:
                                    body = change_value(body, field['destiny'], [])
                    elif field['type'] == 'userslist':
                        exists = get_value_by_path(body, field['destiny'])
                        hasCondition = int(field['conditionField']) if 'conditionField' in field else False
                        conditionField = metadata['fields'][hasCondition] if hasCondition else False
                        if exists:
                            for user in exists:
                                if not isinstance(user, dict) or 'id' not in user:
                                    errors[field['destiny']] = _(u'The field {label} must be a list of users', label=field['label'])
                                else:
                                    user_record = mongodb.get_record('users', {'_id': ObjectId(user['id'])})
                                    if not user_record:
                                        errors[field['destiny']] = _(u'The field {label} must be a list of users', label=field['label'])
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']] = _(u'The field {label} is required', label=field['label'])
                        if hasCondition:
                            if conditionField['type'] == 'checkbox':
                                conditionFieldVal = get_value_by_path(body, conditionField['destiny'])
                                if not conditionFieldVal:
                                    body = change_value(body, field['destiny'], [])
                    elif field['type'] == 'author':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_author_array(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']
                                   ] = _(u'The field {label} is required', label=field['label'])
                    elif field['type'] == 'simple-date':
                        exists = get_value_by_path(body, field['destiny'])
                        hasCondition = int(field['conditionField']) if 'conditionField' in field else False
                        conditionField = metadata['fields'][hasCondition] if hasCondition else False
                        if exists:
                            if isinstance(exists, str):
                                value = get_value_by_path(body, field['destiny'])
                                value = value.replace('"', '')
                                value = parser.isoparse(value)
                                value = value
                            else:
                                value = get_value_by_path(body, field['destiny'])
                            validate_simple_date(value, field)
                            body = change_value(body, field['destiny'], value)
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']] = _(u'The field {label} is required', label=field['label'])
                        if hasCondition:
                            if conditionField['type'] == 'checkbox':
                                conditionFieldVal = get_value_by_path(body, conditionField['destiny'])
                                if not conditionFieldVal:
                                    body = change_value(body, field['destiny'], None)
                    elif field['type'] == 'repeater':
                        value = get_value_by_path(body, field['destiny'])
                        hasCondition = int(field['conditionField']) if 'conditionField' in field else False
                        conditionField = metadata['fields'][hasCondition] if hasCondition else False
                        if value:
                            for v in value:
                                for subfield in field['subfields']:
                                    if subfield['type'] == 'text':
                                        exists = v[subfield['destiny']]
                                        if exists:
                                            subfield['label'] = subfield['name']
                                            validate_text(v[subfield['destiny']], subfield)
                                        elif subfield['required'] and body['status'] == 'published':
                                            errors[subfield['destiny']] = _(u'The field {label} is required', label=subfield['name'])
                                    elif subfield['type'] == 'text-area':
                                        exists = v[subfield['destiny']]
                                        if exists:
                                            subfield['label'] = subfield['name']
                                            validate_text(v[subfield['destiny']], subfield)
                                        elif subfield['required'] and body['status'] == 'published':
                                            errors[subfield['destiny']] = _(u'The field {label} is required', label=subfield['name'])
                                    elif subfield['type'] == 'number':
                                        exists = v[subfield['destiny']]
                                        if exists:
                                            v[subfield['destiny']] = float(v[subfield['destiny']])
                                            if not isinstance(v[subfield['destiny']], numbers.Number):
                                                errors[subfield['destiny']] = _(u'The field {label} must be a number', label=subfield['name'])
                                        elif subfield['required'] and body['status'] == 'published':
                                            errors[subfield['destiny']] = _(u'The field {label} is required', label=subfield['name'])
                                    elif subfield['type'] == 'checkbox':
                                        exists = v[subfield['destiny']]
                                        if exists:
                                            if not isinstance(v[subfield['destiny']], bool):
                                                errors[subfield['destiny']] = _(u'The field {label} must be a boolean', label=subfield['name'])
                                        elif subfield['required'] and body['status'] == 'published':
                                            errors[subfield['destiny']] = _(u'The field {label} is required', label=subfield['name'])
                                    elif subfield['type'] == 'simple-date':
                                        exists = v[subfield['destiny']]
                                        if exists:
                                            if isinstance(exists, str):
                                                value = v[subfield['destiny']]
                                                value = value.replace('"', '')
                                                value = parser.isoparse(value)
                                                value = value
                                            else:
                                                value = v[subfield['destiny']]
                                            
                                            subfield['label'] = subfield['name']
                                            validate_simple_date(value, subfield)
                                            v[subfield['destiny']] = value
                        if hasCondition:
                            if conditionField['type'] == 'checkbox':
                                conditionFieldVal = get_value_by_path(body, conditionField['destiny'])
                                if not conditionFieldVal:
                                    body = change_value(body, field['destiny'], [])
                    elif field['type'] == 'relation':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            value = get_value_by_path(body, field['destiny'])
                            temp = []
                            for f in value:
                                if 'id' not in f:
                                    errors[field['destiny']] = _(u'There is an error in {label}', label=field['label'])
                                else:
                                    resource = mongodb.get_record('resources', {'_id': ObjectId(f['id'])})
                                    if not resource:
                                        errors[field['destiny']] = _(u'There is an error in {label}', label=field['label'])
                                    elif resource['post_type'] != field['relation_type']:
                                        errors[field['destiny']] = _(u'There is an error in {label}', label=field['label'])
                                    else:
                                        temp.append({
                                            'id': f['id'],
                                            'post_type': field['relation_type']
                                        })
                            body = change_value(body, field['destiny'], temp)
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']] = _(u'The field {label} is required', label=field['label'])

        except Exception as e:
            errors[field['destiny']] = str(e)

    if 'accessRights' not in body:
        body['accessRights'] = None
    else:
        if body['accessRights'] == '':
            errors['accessRights'] = _('The resource must have valid access rights')
        elif body['accessRights'] == 'public':
            body['accessRights'] = None
        else:
            access_rights = get_access_rights()
            if 'options' in access_rights:
                access_rights = access_rights['options']
                access_rights = [a['id'] for a in access_rights]

                if body['accessRights'] not in access_rights and body['accessRights'] != None:
                    errors['accessRights'] = _('The resource must have valid access rights')

    return body

def validate_files(files, metadata, errors):
    file_fields = [f for f in metadata['fields'] if f['type'] == 'file']
    count_tags = {}
    for f in files:
        tag = f['tag'] if 'tag' in f else f['filetag'] if 'filetag' in f else 'file'
        # find the field with the same tag
        field = [field for field in file_fields if field['filetag'] == tag]
        if len(field) > 0:
            if field[0]['filetag'] not in count_tags:
                count_tags[field[0]['filetag']] = 1
            else:
                count_tags[field[0]['filetag']] += 1

    for f in file_fields:
        if 'maxFiles' in f:
            if f['maxFiles'] != '' and f['maxFiles'] != 0:
                if f['filetag'] in count_tags:
                    if f['maxFiles'] < count_tags[f['filetag']]:
                        errors[f['filetag']] = _(u'The field {label} must have a maximum of {maxFiles} files', label=f['label'], maxFiles=f['maxFiles'])

# Nuevo servicio para obtener un recurso por su id
def get_by_id(id, user, postQuery = False):
    try:
        # Obtener los accessRights del recurso
        accessRights = get_accessRights(id)
        if accessRights:
            if not has_right(user, accessRights['id']) and not has_role(user, 'admin'):
                return {'msg': _('You don\'t have the required authorization')}, 401
            
        post_type = get_resource_type(id)
        post_type_roles = cache_type_roles(post_type)

        if post_type_roles['viewRoles']:
            canView = False
            for r in post_type_roles['viewRoles']:
                if has_role(user, r) or has_role(user, 'admin'):
                    canView = True
                    break
            if not canView:
                return {'msg': _('You don\'t have the required authorization')}, 401

        resource = get_resource(id, user, postQuery=postQuery)

        register_log(user, log_actions['resource_open'], {'resource': id})

        # Retornar el recurso
        return jsonify(resource), 200
    except Exception as e:
        return {'msg': str(e)}, 500

@cacheHandler.cache.cache(limit=5000)
def get_resource_type(id):
    resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'post_type': 1})
    if not resource:
        raise Exception(_('Resource does not exist'))
    return resource['post_type']

@cacheHandler.cache.cache(limit=5000)
def get_accessRights(id):
    # Buscar el recurso en la base de datos
    resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'accessRights': 1, 'parents': 1})
    # Si el recurso no existe, retornar error
    if not resource:
        raise Exception(_('Resource does not exist'))
    
    if 'accessRights' in resource:
        if resource['accessRights']:
            temp = get_option_by_id(resource['accessRights'])
            return {
                'id': resource['accessRights'],
                'term': temp['term']
            }
        else:
            parents = [ObjectId(item['id']) for item in resource['parents']]
            parents_resources = list(mongodb.get_all_records('resources', {'_id': {'$in': parents}}, fields={'accessRights': 1, 'post_type': 1}))
            
            for r in parents_resources:
                if r['accessRights']:
                    temp = get_option_by_id(r['accessRights'])
                    return {
                        'id': r['accessRights'],
                        'term': temp['term']
                    }
                
        
    return None

@cacheHandler.cache.cache(limit=5000)
def get_resource(id, user, postQuery = False):
    # Buscar el recurso en la base de datos
    resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'updatedAt': 0, 'updatedBy': 0})
    # Si el recurso no existe, retornar error
    if not resource:
        raise Exception(_('Resource does not exist'))
    
    status = resource['status']
    if status == 'draft':
        if not has_role(user, 'publisher') or not has_role(user, 'admin'):
            if resource['createdBy'] != user and not has_role(user, 'editor'):
                raise Exception(_('You don\'t have the required authorization'))
        
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
                'name': _('Asociated files'),
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
                for a in accesRights:
                    if not has_right(user, a) and not has_role(user, 'admin'):
                        canView = False
            
            if not canView:
                set_value_in_dict(resource, f['destiny'], _('You don\'t have the required authorization'))
                temp.append({
                    'label': f['label'],
                    'value': _('You don\'t have the required authorization'),
                    'type': 'text'
                })
                continue
            
            tempTmp = hookHandler.call('resource_field', resource, f, temp)
            if tempTmp:
                temp = tempTmp

            if f['type'] == 'text' or f['type'] == 'text-area':
                value = get_value_by_path(resource, f['destiny'])

                if value:
                    temp.append({
                        'label': f['label'],
                        'value': value,
                        'type': f['type'],
                        'isTitle': f.get('destiny', False) == 'metadata.firstLevel.title'
                    })
            elif f['type'] == 'select':
                value = get_value_by_path(resource, f['destiny'])
                value = get_option_by_id(value)
                if value and 'term' in value:
                    temp.append({
                        'label': f['label'],
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
            elif f['type'] == 'number':
                value = get_value_by_path(resource, f['destiny'])
                if value:
                    temp.append({
                        'label': f['label'],
                        'value': value,
                        'type': 'number'
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
            elif f['type'] == 'location':
                value = get_value_by_path(resource, f['destiny'])
                if value:
                    temp.append({
                        'label': f['label'],
                        'value': value,
                        'type': 'location'
                    })
            elif f['type'] == 'repeater':
                value = get_value_by_path(resource, f['destiny'])
                if value:
                    temp_ = []
                    for v in value:
                        temp__ = []
                        for s in f['subfields']:
                            if s['type'] == 'text' or s['type'] == 'text-area':
                                temp__.append({
                                    'label': s['name'],
                                    'value': v[s['destiny']],
                                    'type': s['type']
                                })
                            elif s['type'] == 'number':
                                temp__.append({
                                    'label': s['name'],
                                    'value': v[s['destiny']],
                                    'type': s['type']
                                })
                            elif s['type'] == 'checkbox':
                                temp__.append({
                                    'label': s['name'],
                                    'value': v[s['destiny']],
                                    'type': s['type']
                                })
                            elif s['type'] == 'simple-date':
                                if isinstance(v[s['destiny']], datetime):
                                    v[s['destiny']] = v[s['destiny']].strftime('%Y-%m-%d')
                                temp__.append({
                                    'label': s['name'],
                                    'value': v[s['destiny']],
                                    'type': s['type']
                                })
                        temp_.append(temp__)

                    temp.append({
                        'label': f['label'],
                        'value': temp_,
                        'type': 'repeater'
                    })
            

    resource['fields'] = temp
    
    if postQuery:
        resource_tmp = hookHandler.call('get_resource_post', resource)
        if resource_tmp:
            resource = resource_tmp
    else:
        resource_tmp = hookHandler.call('get_resource', resource)
        if resource_tmp:
            resource = resource_tmp
        
    resource['accessRights'] = get_option_by_id(resource['accessRights'])
    if resource['accessRights'] and 'term' in resource['accessRights']:
        resource['accessRights'] = str(resource['accessRights']['_id'])
    else:
        resource['accessRights'] = None

    if 'createdAt' in resource:
        resource['createdAt'] = resource['createdAt'].isoformat()

    return resource

@cacheHandler.cache.cache(limit=1000)
def get_resource_files(id, user, page, groupImages = False):
    try:
        resource = mongodb.get_record('resources', {'_id': ObjectId(id)})
        # check if the user has access to the resource
        accessRights = get_accessRights(id)
        if accessRights:
            if not has_right(user, accessRights['id']) and not has_role(user, 'admin'):
                return {'msg': _('You don\'t have the required authorization')}, 401
        # Si el recurso no existe, retornar error
        if not resource:
            return {'msg': _('Resource does not exist')}, 404

        temp = []
        ids = []
        
        imgsTotal = 0
        if 'filesObj' in resource:
            for r in resource['filesObj']:
                ids.append(r)

        r_ = get_resource_records(json.dumps(ids), user, page, groupImages=groupImages)
        for _ in r_:
            if _['_id'] == 'imgGallery':
                imgsTotal = int(_['displayName'].split(' ')[0])

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

        total = len(resource['filesObj'])
        if imgsTotal > 0:
            total = total - imgsTotal + 1
            
        resp = {
            'data': temp,
            'total': total
        }
        # Retornar el recurso
        return resp, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
def download_resource_files(body, user):
    try:
        from app.api.system.services import get_system_settings
        settings, status = get_system_settings()
        capabilities = settings['capabilities']
        
        if 'files_download' not in capabilities:
            return {'msg': _('Files download isn\'t active')}, 400
        
        resource = mongodb.get_record('resources', {'_id': ObjectId(body['id'])})
        # check if the user has access to the resource
        accessRights = get_accessRights(body['id'])
        if accessRights:
            if not has_right(user, accessRights['id']) and not has_role(user, 'admin'):
                return {'msg': _('You don\'t have the required authorization')}, 401
        # Si el recurso no existe, retornar error
        if not resource:
            return {'msg': _('Resource does not exist')}, 404

        temp = []
        ids = []
        
        if 'filesObj' in resource:
            for r in resource['filesObj']:
                ids.append(r)
                
        if len(ids) > 1:
            r_ = get_resource_records(json.dumps(ids), user, 0, None, False)
            zippath = os.path.join(WEB_FILES_PATH, 'zipfiles', user + '-' + body['id'] + '-' + body['type'] + '.zip')
            
            if not os.path.exists(zippath):
                os.makedirs(os.path.dirname(zippath), exist_ok=True)

                import zipfile
                zipf = zipfile.ZipFile(zippath, 'w', zipfile.ZIP_DEFLATED)
                
                for re in r_:
                    if re['filepath']:
                        if body['type'] == 'original':
                            path = os.path.join(ORIGINAL_FILES_PATH, re['filepath'])
                            zipf.write(path, re['name'])
                            
                        elif body['type'] == 'small':
                            path = os.path.join(WEB_FILES_PATH, re['processing']['fileProcessing']['path'])
                            
                            if re['processing']['fileProcessing']['type'] == 'image':
                                path = path + '_large.jpg'
                            elif re['processing']['fileProcessing']['type'] == 'audio':
                                path = path + '.mp3'
                            elif re['processing']['fileProcessing']['type'] == 'video':
                                path = path + '.mp4'
                            elif re['processing']['fileProcessing']['type'] == 'document':
                                path = os.path.join(ORIGINAL_FILES_PATH, re['filepath'])
                            zipf.write(path, re['name'])
                        
                zipf.close()
            
            
            return send_file(zippath, as_attachment=True)
        elif len(ids) == 1:
            r_ = mongodb.get_record('records', {'_id': ObjectId(ids[0]['id'])})
            if not r_:
                return {'msg': _('File does not exist')}, 404
            
            if body['type'] == 'original':
                path = os.path.join(ORIGINAL_FILES_PATH, r_['filepath'])
                filename = r_['name']
            elif body['type'] == 'small':
                path = os.path.join(WEB_FILES_PATH, r_['processing']['fileProcessing']['path'])
                
                if r_['processing']['fileProcessing']['type'] == 'image':
                    path = path + '_large.jpg'
                elif r_['processing']['fileProcessing']['type'] == 'audio':
                    path = path + '.mp3'
                elif r_['processing']['fileProcessing']['type'] == 'video':
                    path = path + '.mp4'
                elif r_['processing']['fileProcessing']['type'] == 'document':
                    path = os.path.join(ORIGINAL_FILES_PATH, r_['filepath'])
                    
                filename = r_['name']
                
            return send_file(path, as_attachment=True, download_name=filename)
                    
    except Exception as e:
        return {'msg': str(e)}, 500
    
def delete_zip_files():
    try:
        zippath = os.path.join(WEB_FILES_PATH, 'zipfiles')
        if not os.path.exists(zippath):
            os.makedirs(zippath, exist_ok=True)
        for f in os.listdir(zippath):
            os.remove(os.path.join(zippath, f))
            
        return {'msg': _('Zip files deleted')}, 200
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500
    
def delete_inventory_files():
    try:
        inventories = os.path.join(WEB_FILES_PATH, 'inventoryMaker')
        if not os.path.exists(inventories):
            os.makedirs(inventories, exist_ok=True)
        for f in os.listdir(inventories):
            os.remove(os.path.join(inventories, f))
            
        return {'msg': _('Inventory files deleted')}, 200
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500

# Nuevo servicio para eliminar un recurso
def delete_by_id(id, user):
    try:
        post_type = get_resource_type(id)
        post_type_roles = cache_type_roles(post_type)

        if post_type_roles['editRoles']:
            canEdit = False
            for r in post_type_roles['editRoles']:
                if has_role(user, r) or has_role(user, 'admin'):
                    canEdit = True
                    break
            if not canEdit:
                return {'msg': _('You don\'t have the required authorization')}, 401
        
        if post_type_roles['viewRoles']:
            canView = False
            for r in post_type_roles['viewRoles']:
                if has_role(user, r) or has_role(user, 'admin'):
                    canView = True
                    break
            if not canView:
                return {'msg': _('You don\'t have the required authorization')}, 401

        resource = mongodb.get_record('resources', {'_id': ObjectId(id)})
        
        if resource['createdBy'] != user and not has_role(user, 'admin') and not has_role(user, 'super_editor'):
            return {'msg': _('You don\'t have the required authorization')}, 401
        
        if 'files' in resource:
            records_list = resource['files']
            delete_records(records_list, id, user)


        delete_children(id)
        # Eliminar el recurso de la base de datos
        deleted_resource = mongodb.delete_record('resources', {'_id': ObjectId(id)})

        hookHandler.call('resource_delete', {'_id': id})
        # Eliminar los hijos del recurso
        # Registrar el log
        register_log(user, log_actions['resource_delete'], {'resource': id})
        # limpiar la cache
        update_cache()

        # Retornar el resultado
        return {'msg': _('Resource deleted')}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
@cacheHandler.cache.cache()
def get_resource_images(id, user):
    resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'filesObj': 1})

    if not resource:
        return {'msg': _('Resource does not exist')}, 404

    ids = []
    if 'filesObj' in resource:
        for r in resource['filesObj']:
            ids.append(r['id'])

    img = mongodb.count('records', {'_id': {'$in': [ObjectId(id) for id in ids]}, 'processing.fileProcessing.type': 'image'})
    if img == 0:
        return {'msg': _('Resource does not have images')}, 404
    
    resp = {
        'pages': img
    }

    return resp, 200

# Funcion para obtener los hijos de un recurso
@cacheHandler.cache.cache(limit=3000)
def get_children(id, available, resp=False, post_type=None, status='published'):
    try:
        list_available = available.split('|')
        if post_type:
            list_available = [post_type]

        # Obtener los recursos del tipo de contenido
        if not resp:
            resources = mongodb.get_record('resources', {'post_type': {
                                           '$in': list_available}, 'parents.post_type': {'$in': available.split('|')}, 'parents.id': id, 'status': status})
        else:
            resources = mongodb.get_all_records('resources', {'post_type': {
                                                '$in': list_available}, 'parents.post_type': {'$in': available.split('|')}, 'parents.id': id, 'status': status}, limit=10)

        if (resources and not resp):
            return True
        elif not resp:
            return False

        if not resources:
            return []
        else:
            resources = [{'name': re['metadata']['firstLevel']['title'],
                          'post_type': re['post_type'], 'id': str(re['_id'])} for re in resources]
            return resources
    except Exception as e:
        return {'msg': str(e)}, 500

@cacheHandler.cache.cache(limit=15000)
def get_children_cache(root, available, post_type=None):
    list_available = available.split('|')

    fields = {'metadata.firstLevel.title': 1, 'post_type': 1}

    resp = []

    if root == 'all':
        
        resources = list(mongodb.get_all_records('resources', {
                            'post_type': {
                            "$in": list_available}, 'parent': None, 'status': 'published'}, sort=[('metadata.firstLevel.title', 1)], fields=fields, limit=35))
    else:
        resources = list(mongodb.get_all_records('resources', {'post_type': {
                            "$in": list_available}, 'parent.id': root, 'status': 'published'}, sort=[('metadata.firstLevel.title', 1)], fields=fields, limit=35))
        
        resources = [{'name': re['metadata']['firstLevel']['title'], 'post_type': re['post_type'], 'id': str(
            re['_id'])} for re in resources]

        for resource in resources:
            resource['children'] = get_children(resource['id'], available, False, post_type)
            if resource['children']:
                resource['icon'] = get_icon(resource['post_type'])
                resp.append(resource)

    return resp
    

# Funcion para obtener los hijos de un recurso en forma de arbol
@cacheHandler.cache.cache(limit=5000)
def get_tree(root, available, user, post_type=None, page=None, status='published'):
    try:
        list_available = available.split('|')

        fields = {'metadata.firstLevel.title': 1, 'post_type': 1, 'parent': 1}
        status_ = 'published'
        if status == 'draft':
            status_ = {'$in': ['draft', 'published']}

        if root == 'all':
            if page is not None:
                
                resources = list(mongodb.get_all_records('resources', {
                             'post_type': {
                             "$in": list_available}, 'parent': {'$in': [None, []]}, 'status': status_}, sort=[('metadata.firstLevel.title', 1)], fields=fields, limit=10, skip=page * 10))
            else:
                resources = list(mongodb.get_all_records('resources', {
                             'post_type': {
                             "$in": list_available}, 'parent': {'$in': [None, []]}, 'status': status_}, sort=[('metadata.firstLevel.title', 1)], fields=fields))
        else:
            if page is not None:
                resources = list(mongodb.get_all_records('resources', {'post_type': {
                             "$in": list_available}, 'parent.id': root, 'status': status_}, sort=[('metadata.firstLevel.title', 1)], fields=fields, limit=10, skip=page * 10))
            else:
                resources = list(mongodb.get_all_records('resources', {'post_type': {
                             "$in": list_available}, 'parent.id': root, 'status': status_}, sort=[('metadata.firstLevel.title', 1)], fields=fields))

        resources = [{'name': re['metadata']['firstLevel']['title'], 'post_type': re['post_type'], 'id': str(
            re['_id'])} for re in resources]
        
        for resource in resources:
            resource['children'] = get_children(resource['id'], available, False, post_type, status=status)
            resource['icon'] = get_icon(resource['post_type'])
            resource['type'] = get_by_slug(resource['post_type'])
            name = resource['type']['name']
            resource['type'] = name

        # Retornar los recursos y los padres
        return resources, 200
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500

# Funcion para validar que el tipo del padre sea uno admitido por el hijo
@cacheHandler.cache.cache(limit=1000)
def has_parent_postType(post_type, compare):
    try:
        # Obtener el tipo de post
        post_type = mongodb.get_record('post_types', {'slug': post_type})
        # Si el tipo de post no existe, retornar error
        if not post_type:
            return {'msg': _('Post type does not exist')}, 404
        # Si el tipo de post tiene padre, retornar True
        if len(post_type['parentType']) > 0:
            for p in post_type['parentType']:
                if p['id'] == compare:
                    return True
                if p['hierarchical'] and p['id'] != compare:
                    return True

        # Si el tipo de post no tiene padre, retornar False
        return False
    except Exception as e:
        return {'msg': str(e)}, 500

# Funcion para obtener los padres de un recurso
@cacheHandler.cache.cache(limit=1000)
def get_parents(id, level=1):
    try:
        # Buscar el recurso en la base de datos
        resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'parent': 1})
        # Si el recurso no existe, retornar error
        if not resource:
            return []
        # Si el recurso no tiene padre, retornar una lista vacia
        if 'parent' not in resource:
            return []
        else:
            if resource['parent']:
                parent = resource['parent']
                if isinstance(parent, dict):
                    parent = [parent]
                
                all_ancestors = []

                for p in parent:
                    p['level'] = level
                    p['parentOf'] = [id]
                    all_ancestors.extend(get_parents(p['id'], level + 1))
                
                ancestor_ids = {ancestor['id'] for ancestor in all_ancestors}
                final_direct_parents = [p for p in parent if p['id'] not in ancestor_ids]
                
                
                parents = []
                for p in final_direct_parents:
                    parents.append(p)
                    parents = [*parents, *get_parents(p['id'], level + 1)]
                    
                unique_parents = []
                seen_ids = set()
                for p in parents:
                    if p['id'] not in seen_ids:
                        unique_parents.append(p)
                        seen_ids.add(p['id'])

                return unique_parents
            else:
                return []
    except Exception as e:
        raise Exception(str(e))

# Funcion para obtener el padre directo de un recurso
@cacheHandler.cache.cache(limit=1000)
def get_parent(id):
    try:
        # Buscar el recurso en la base de datos
        resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'parent': 1})
        # Si el recurso no existe, retornar error
        if not resource:
            return {'msg': _('Resource does not exist')}, 404
        # Si el recurso no tiene padre, retornar una lista vacia
        if 'parent' not in resource:
            return None
        else:
            if resource['parent']:
                parent = resource['parent']
                if isinstance(parent, dict):
                    return [parent]
                elif isinstance(parent, list):
                    return parent
            else:
                return []
    except Exception as e:
        raise Exception(str(e))

# Funcion para determinar si un recurso cambio de padre
def has_changed_parent(id, body):
    try:
        parent = get_parent(id)
        new_parent = body['parent']
        
        if not parent and not new_parent:
            return False
        if not parent or not new_parent:
            return True
        if len(parent) != len(new_parent):
            return True
        
        parent_ids = {p['id'] for p in parent}
        new_parent_ids = {p['id'] for p in new_parent}
        return parent_ids != new_parent_ids
    except Exception as e:
        raise Exception(str(e))

# Funcion para obtener los hijos directos de un recurso
def get_direct_children(id):
    try:
        # Buscar los recursos en la base de datos con parent igual al id
        resources = list(mongodb.get_all_records(
            'resources', {'parent.id': id}, fields={'_id': 1, 'post_type': 1, 'parent': 1}))

        resources = [{'post_type': re['post_type'],
                      'id': str(re['_id']), 'parent': re['parent']} for re in resources]

        return resources

    except Exception as e:
        raise Exception(str(e))

# Funcion para actualizar los padres recursivamente
def update_parents(id, post_type, user):
    try:
        get_parents.invalidate_all()
        get_parent.invalidate_all()
        # Hijos directos del recurso
        children = get_direct_children(id)
        # Si el recurso tiene hijos directos, actualizar el parent de cada hijo
        if children:
            for child in children:
                parent = child['parent']
                if isinstance(parent, dict):
                    parent = [parent]
                
                all_ancestors = []

                for p in parent:
                    all_ancestors.extend(get_parents(p['id']))
                
                ancestor_ids = {ancestor['id'] for ancestor in all_ancestors}
                final_direct_parents = [p for p in parent if p['id'] not in ancestor_ids]
                
                
                parents = []
                for p in final_direct_parents:
                    parents.append(p)
                    parents = [*parents, *get_parents(p['id'])]
                    
                unique_parents = []
                seen_ids = set()
                for p in parents:
                    if p['id'] not in seen_ids:
                        unique_parents.append(p)
                        seen_ids.add(p['id'])

                update = ResourceUpdate(**{'parents': unique_parents, 'updatedBy': user, 'updatedAt': datetime.now()})
                mongodb.update_record(
                    'resources', {'_id': ObjectId(child['id'])}, update)
                update_parents(child['id'], child['post_type'], user)

    except Exception as e:
        raise Exception(str(e))

# Funcion para actualizar los padres recursivamente de los records


def update_records_parents(id, user):
    try:
        # Hijos directos del recurso
        children = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'filesObj': 1})
        if not children or 'filesObj' not in children:
            return
        children = children['filesObj']
        # Si el recurso tiene hijos directos, actualizar el parent de cada hijo
        if children:
            for child in children:
                record = mongodb.get_record(
                    'records', {'_id': ObjectId(child['id'])})
                parents = record['parent']
                temp = []

                for p in parents:
                    temp = [*temp, *get_parents(p['id'])]

                update_parent(child['id'], user, temp)

    except Exception as e:
        raise Exception(str(e))

# Funcion para eliminar los hijos recursivamente


def delete_children(id):
    try:
        # Hijos directos del recurso
        children = get_direct_children(id)
        # Si el recurso tiene hijos directos, eliminar cada hijo
        if children:
            for child in children:
                delete_children(child['id'])
                # buscar el recurso en la base de datos
                resource = mongodb.get_record(
                    'resources', {'_id': ObjectId(child['id'])}, fields={'files': 1})
                # si el recurso tiene archivos, eliminarlos
                if 'filesObj' in resource:
                    records_list = resource['filesObj']
                    records_list = [r['id'] for r in records_list]
                    delete_records(records_list, child['id'], None)
                # eliminar el recurso de la base de datos
                mongodb.delete_record(
                    'resources', {'_id': ObjectId(child['id'])})
                
                hookHandler.call('resource_delete', {'_id': child['id']})

    except Exception as e:
        raise Exception(str(e))


def delete_records(list, resource_id, user):
    try:
        for l in list:
            delete_parent(resource_id, l, user)
    except Exception as e:
        raise Exception(str(e))


def update_records(list, user):
    try:
        for l in list:
            update_record(l, user)
    except Exception as e:
        raise Exception(str(e))
    
def add_to_favCount(id):
    try:
        update = {
            '$inc': {
                'favCount': 1
            }
        }
        update_ = ResourceUpdate(**update)
        mongodb.update_record('resources', {'_id': ObjectId(id)}, update_)
        get_favCount.invalidate(id)
    except Exception as e:
        raise Exception(str(e))
    
def remove_from_favCount(id):
    try:
        update = {
            '$inc': {
                'favCount': -1
            }
        }
        update_ = ResourceUpdate(**update)
        mongodb.update_record('resources', {'_id': ObjectId(id)}, update_)
        get_favCount.invalidate(id)
    except Exception as e:
        raise Exception(str(e))

@cacheHandler.cache.cache(limit=2000)
def get_favCount(id):
    try:
        resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'favCount': 1})
        if not resource:
            raise Exception(_('Resource does not exist'))
        return {'favCount': resource['favCount']}, 200
    except Exception as e:
        raise Exception(str(e))

# Funcion para obtener el total de recursos
@cacheHandler.cache.cache(limit=1000)
def get_total(obj):
    try:
        # convertir string a dict
        obj = json.loads(obj)
        # Obtener el total de recursos
        total = mongodb.count('resources', obj)
        # Retornar el total
        return total
    except Exception as e:
        raise Exception(str(e))
    
def change_post_type(body, user):
    try:
        post_type = get_resource_type(body['id'])
        post_type_roles = cache_type_roles(post_type)

        if post_type_roles['editRoles']:
            canEdit = False
            for r in post_type_roles['editRoles']:
                if has_role(user, r) or has_role(user, 'admin'):
                    canEdit = True
                    break
            if not canEdit:
                return {'msg': _('You don\'t have the required authorization')}, 401

        

        # Retornar el resultado
        return {'msg': _('Post type changed')}, 200
    except Exception as e:
        return {'msg': str(e)}, 500

def update_cache():
    get_access_rights.invalidate_all()
    get_resource.invalidate_all()
    get_children.invalidate_all()
    get_tree.invalidate_all()
    has_parent_postType.invalidate_all()
    get_parents.invalidate_all()
    get_parent.invalidate_all()
    get_total.invalidate_all()
    get_accessRights.invalidate_all()
    get_resource_type.invalidate_all()
    get_resource_files.invalidate_all()
    get_all.invalidate_all()
    get_resource_images.invalidate_all()
    get_children_cache.invalidate_all()
    clear_cache()