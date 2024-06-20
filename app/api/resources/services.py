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
from app.api.users.services import has_right, has_role
from app.utils.functions import get_resource_records, cache_type_roles, clear_cache
import os
from datetime import datetime
from dateutil import parser
mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()
hookHandler = HookHandler.HookHandler()
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
def get_all(post_type, body, user):
    try:
        body = json.loads(body)
        post_type_roles = cache_type_roles(post_type)
        if post_type_roles['viewRoles']:
            canView = False
            for r in post_type_roles['viewRoles']:
                if has_role(user, r) or has_role(user, 'admin'):
                    canView = True
                    break
            if not canView:
                return {'msg': 'No tiene permisos para obtener los recursos'}, 401

        filters = {}
        limit = 20
        skip = 0
        filters['post_type'] = post_type

        if 'parents' in body:
            if body['parents']:
                filters['parents.id'] = body['parents']['id']

        if 'status' not in body:
            body['status'] = 'published'

        if 'page' in body:
            skip = body['page'] * limit

        filters['status'] = body['status']

        if filters['status'] == 'draft':
            filters.pop('status')
            filters_ = {'$or': [{'status': 'draft', **filters}, {'status': 'created', **filters}, {'status': 'updated', **filters}]}
            if not has_role(user, 'publisher') or not has_role(user, 'admin'):
                for o in filters_['$or']:
                    o['createdBy'] = user
            filters = filters_

        # Obtener todos los recursos dado un tipo de contenido
        resources = list(mongodb.get_all_records(
            'resources', filters, limit=limit, skip=skip, fields={'metadata.firstLevel.title': 1, 'accessRights': 1}, sort=[('metadata.firstLevel.title', 1)]))
        # Obtener el total de recursos dado un tipo de contenido
        total = get_total(json.dumps(filters))
        # Para cada recurso, obtener el formulario asociado y quitar los campos _id
        for resource in resources:
            resource['id'] = str(resource['_id'])
            resource.pop('_id')
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

# Nuevo servicio para crear un recurso
def create(body, user, files):
    try:
        # si el body tiene parents, verificar que el recurso sea jerarquico
        body = validate_parent(body)
        # Si el body no tiene metadata, retornar error
        if 'metadata' not in body:
            return {'msg': 'El recurso debe tener metadata'}, 400

        status = body['status']
        if status == 'published':
            if not has_role(user, 'publisher') and not has_role(user, 'admin'):
                return {'msg': 'No tiene permisos para publicar un recurso'}, 401
            
        if not status:
            status = 'draft'

        body['createdBy'] = user
            
        # Obtener los metadatos en función del tipo de contenido
        metadata = get_metadata(body['post_type'])

        errors = {}
        # Validar los campos de la metadata
        body = validate_fields(body, metadata, errors)

        update_relations_children(body, metadata['fields'], True)

        if 'ident' not in body:
            body['ident'] = 'ident'
        
        hookHandler.call('resource_ident_create', body)

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
        # Crear instancia de Resource con el body del request

        resource = Resource(**body)

        # Insertar el recurso en la base de datos
        new_resource = mongodb.insert_record('resources', resource)
        body['_id'] = str(new_resource.inserted_id)
        # Registrar el log
        register_log(user, log_actions['resource_create'], {'resource': body})

        # limpiar la cache
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
                'filesObj': records
            }

            print(update)

            update_ = ResourceUpdate(**update)

            mongodb.update_record(
                'resources', {'_id': ObjectId(body['_id'])}, update_)
            
            # limpiar la cache
            update_cache()
            
            hookHandler.call('resource_files_create', body)

        # Retornar el resultado
        return {'msg': 'Recurso creado exitosamente'}, 201
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500

def update_by_id(id, body, user, files):
    try:
        body = validate_parent(body)
        has_new_parent = has_changed_parent(id, body)
        # Obtener los metadatos en función del tipo de contenido
        metadata = get_metadata(body['post_type'])

        errors = {}

        # Validar los campos de la metadata
        body = validate_fields(body, metadata, errors)

        update_relations_children(body, metadata['fields'])

        if errors:
            return {'msg': 'Error al validar los campos', 'errors': errors}, 400
        
        resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'files': 1, 'filesObj': 1})

        validate_files([*body['filesIds'], *resource['filesObj']], metadata, errors)

        if errors:
            return {'msg': 'Error al validar los archivos', 'errors': errors}, 400

        status = body['status']
        if status == 'published':
            if not has_role(user, 'publisher') and not has_role(user, 'admin'):
                return {'msg': 'No tiene permisos para publicar un recurso'}, 401
        if not status:
            status = 'draft'

        temp = []
        temp_files_obj = body['filesIds']

        if 'filesObj' in resource:
            for f in resource['filesObj']:
                temp.append(f)

        body['filesObj'] = temp
        del body['filesIds']

        # Crear instancia de ResourceUpdate con el body del request
        resource = ResourceUpdate(**body)

        # Actualizar el recurso en la base de datos
        updated_resource = mongodb.update_record(
            'resources', {'_id': ObjectId(id)}, resource)

        if has_new_parent:
            update_parents(id, body['post_type'])
            update_records_parents(id, user)

        try:
            records = create_record(id, user, files, filesTags = temp_files_obj)
        except Exception as e:
            print(str(e))
            return {'msg': str(e)}, 500

        delete_records(body['deletedFiles'], id, user)
        update_records(body['updatedFiles'], user)
        
        body['filesObj'] = [f for f in body['filesObj'] if f['id'] not in body['deletedFiles']]

        update = {
            'filesObj': [*body['filesObj'], *records]
        }

        seen = set()
        new_list = []
        for d in update['filesObj']:
            t = tuple(d.items())
            if t not in seen:
                seen.add(t)
                new_list.append(d)
        update['filesObj'] = new_list

        update_ = ResourceUpdate(**update)

        mongodb.update_record(
            'resources', {'_id': ObjectId(body['_id'])}, update_)
        
        hookHandler.call('resource_update', body)

        # Registrar el log
        register_log(user, log_actions['resource_update'], {'resource': body})
        # limpiar la cache
        update_cache()
        # Retornar el resultado
        return {'msg': 'Recurso actualizado exitosamente'}, 200
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
def validate_parent(body):
    if 'parents' in body:
        hierarchical = is_hierarchical(body['post_type'])
        if body['parents']:
            parent = body['parents'][0]
            # si el padre es el mismo que el hijo, retornar error
            if '_id' in body:
                if parent['id'] == body['_id']:
                    raise Exception('El recurso no puede ser su propio padre')
            
            if 'post_type' not in parent:
                parent_temp = mongodb.get_record('resources', {'_id': ObjectId(parent['id'])}, fields={'post_type': 1})
                if not parent_temp:
                    raise Exception('El recurso padre no existe')
                parent['post_type'] = parent_temp['post_type']
            # si el tipo del padre es el mismo que el del hijo y no es jerarquico, retornar error
            if parent['post_type'] == body['post_type'] and not hierarchical[0]:
                raise Exception('El tipo de contenido no es jerarquico')
            # si el tipo del padre es diferente al del hijo y el hijo no lo tiene como padre, retornar error
            elif not has_parent_postType(body['post_type'], parent['post_type']) and not hierarchical[0]:
                raise Exception(
                    'El recurso no tiene como padre al recurso padre')

            body['parents'] = [parent, *get_parents(parent['id'])]
            body['parent'] = parent
            return body
        else:
            if hierarchical[0] and hierarchical[1]:
                body['parents'] = []
                body['parent'] = None
                return body
            elif hierarchical[0] and not hierarchical[1]:
                body['parents'] = []
                body['parent'] = None
                return body
            elif not hierarchical[0] and hierarchical[1]:
                raise Exception('El tipo de contenido debe tener un padre')
            elif not hierarchical[0] and not hierarchical[1]:
                body['parents'] = []
                body['parent'] = None
                return body
    else:
        body['parents'] = []
        body['parent'] = None
        return body

# Funcion para validar los campos de la metadata
def validate_fields(body, metadata, errors):
    for field in metadata['fields']:
        try:
            if field['type'] != 'file' and field['type'] != 'separator':
                if field['destiny'] != 'ident':
                    if field['destiny'] == 'metadata.firstLevel.title':
                        value = get_value_by_path(body, field['destiny'])
                        if not value or value == '':
                            if field['required'] and body['status'] == 'published':
                                errors[field['destiny']] = f'El campo {field["label"]} es requerido'
                            else:
                                body = change_value(body, field['destiny'], 'Sin título')
                    if field['type'] == 'text':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_text(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']
                                   ] = f'El campo {field["label"]} es requerido'
                    elif field['type'] == 'text-area':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_text(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']
                                   ] = f'El campo {field["label"]} es requerido'
                    elif field['type'] == 'select':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_text(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required'] and body['status'] == 'published' and field['destiny'] != 'accessRights':
                            errors[field['destiny']
                                   ] = f'El campo {field["label"]} es requerido'
                    elif field['type'] == 'number':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            if not isinstance(get_value_by_path(body, field['destiny']), int):
                                errors[field['destiny']
                                    ] = f'El campo {field["label"]} debe ser un número'
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']
                                   ] = f'El campo {field["label"]} es requerido'
                    elif field['type'] == 'checkbox':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            if not isinstance(get_value_by_path(body, field['destiny']), bool):
                                errors[field['destiny']
                                    ] = f'El campo {field["label"]} debe ser un booleano'
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']
                                   ] = f'El campo {field["label"]} es requerido'
                    elif field['type'] == 'select-multiple2':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_text_array(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']
                                   ] = f'El campo {field["label"]} es requerido'
                    elif field['type'] == 'author':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_author_array(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']
                                   ] = f'El campo {field["label"]} es requerido'
                    elif field['type'] == 'simple-date':
                        exists = get_value_by_path(body, field['destiny'])
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
                            errors[field['destiny']] = f'El campo {field["label"]} es requerido'
                    elif field['type'] == 'relation':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            value = get_value_by_path(body, field['destiny'])
                            temp = []
                            for f in value:
                                if 'id' not in f:
                                    errors[field['destiny']] = f'Hay un error en el campo {field["label"]}'
                                else:
                                    resource = mongodb.get_record('resources', {'_id': ObjectId(f['id'])})
                                    if not resource:
                                        errors[field['destiny']] = f'Hay un error en el campo {field["label"]}'
                                    elif resource['post_type'] != field['relation_type']:
                                        errors[field['destiny']] = f'Hay un error en el campo {field["label"]}'
                                    else:
                                        temp.append({
                                            'id': f['id'],
                                            'post_type': field['relation_type']
                                        })
                            body = change_value(body, field['destiny'], temp)
                        elif field['required'] and body['status'] == 'published':
                            errors[field['destiny']] = f'El campo {field["label"]} es requerido'

        except Exception as e:
            print(str(e), field['destiny'])
            errors[field['destiny']] = str(e)

    if 'accessRights' not in body:
        body['accessRights'] = None
    else:
        if body['accessRights'] == '':
            errors['accessRights'] = 'El recurso debe tener derechos de acceso'
        
        elif body['accessRights'] == 'public':
            body['accessRights'] = None
        else:
            access_rights = get_access_rights()
            if 'options' in access_rights:
                access_rights = access_rights['options']
                access_rights = [a['id'] for a in access_rights]

                if body['accessRights'] not in access_rights and body['accessRights'] != None:
                    errors['accessRights'] = 'El recurso debe tener derechos de acceso válidos'

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
                        errors[f['filetag']] = f'El campo {f["label"]} no puede tener más de {f["maxFiles"]} archivos'

# Nuevo servicio para obtener un recurso por su id
def get_by_id(id, user):
    try:
        # Obtener los accessRights del recurso
        accessRights = get_accessRights(id)
        if accessRights:
            if not has_right(user, accessRights['id']) and not has_role(user, 'admin'):
                return {'msg': 'No tiene permisos para acceder al recurso'}, 401
            
        post_type = get_resource_type(id)
        post_type_roles = cache_type_roles(post_type)

        if post_type_roles['viewRoles']:
            canView = False
            for r in post_type_roles['viewRoles']:
                if has_role(user, r) or has_role(user, 'admin'):
                    canView = True
                    break
            if not canView:
                return {'msg': 'No tiene permisos para obtener un recurso'}, 401

        resource = get_resource(id, user)

        register_log(user, log_actions['resource_open'], {'resource': id})

        # Retornar el recurso
        return jsonify(resource), 200
    except Exception as e:
        return {'msg': str(e)}, 500

@cacheHandler.cache.cache(limit=5000)
def get_resource_type(id):
    resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'post_type': 1})
    if not resource:
        raise Exception('Recurso no existe')
    return resource['post_type']

@cacheHandler.cache.cache(limit=5000)
def get_accessRights(id):
    # Buscar el recurso en la base de datos
    resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'accessRights': 1, 'parents': 1})
    # Si el recurso no existe, retornar error
    if not resource:
        raise Exception('Recurso no existe')
    
    if 'accessRights' in resource:
        if resource['accessRights']:
            temp = get_option_by_id(resource['accessRights'])
            return {
                'id': resource['accessRights'],
                'term': temp['term']
            }
        else:
            parents = [ObjectId(item['id']) for item in resource['parents']]
            parents_resources = list(mongodb.get_all_records('resources', {'_id': {'$in': parents}}, fields={'accessRights': 1}))
            
            for r in parents_resources:
                if r['accessRights']:
                    temp = get_option_by_id(r['accessRights'])
                    return {
                        'id': r['accessRights'],
                        'term': temp['term']
                    }
                
        
    return None

@cacheHandler.cache.cache(limit=5000)
def get_resource(id, user):
    # Buscar el recurso en la base de datos
    resource = mongodb.get_record('resources', {'_id': ObjectId(id)})
    # Si el recurso no existe, retornar error
    if not resource:
        raise Exception('Recurso no existe')
    
    status = resource['status']
    if status == 'draft':
        if not has_role(user, 'publisher') or not has_role(user, 'admin'):
            if resource['createdBy'] != user:
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
                for a in accesRights:
                    if not has_right(user, a) and not has_role(user, 'admin'):
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

    return resource

@cacheHandler.cache.cache(limit=1000)
def get_resource_files(id, user, page, groupImages = False):
    try:
        resource = mongodb.get_record('resources', {'_id': ObjectId(id)})
        # Si el recurso no existe, retornar error
        if not resource:
            return {'msg': 'Recurso no existe'}, 404

        temp = []
        ids = []
        if 'filesObj' in resource:
            for r in resource['filesObj']:
                ids.append(r)

        r_ = get_resource_records(json.dumps(ids), user, page, groupImages=groupImages)
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
            'total': len(ids)
        }
        # Retornar el recurso
        return resp, 200
    except Exception as e:
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
                return {'msg': 'No tiene permisos para eliminar un recurso'}, 401
        
        if post_type_roles['viewRoles']:
            canView = False
            for r in post_type_roles['viewRoles']:
                if has_role(user, r) or has_role(user, 'admin'):
                    canView = True
                    break
            if not canView:
                return {'msg': 'No tiene permisos para eliminar un recurso'}, 401

        resource = mongodb.get_record('resources', {'_id': ObjectId(id)})
        
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
        return {'msg': 'Recurso eliminado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
@cacheHandler.cache.cache()
def get_resource_images(id, user):
    resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'filesObj': 1})

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

# Funcion para obtener los hijos de un recurso
@cacheHandler.cache.cache(limit=1000)
def get_children(id, available, resp=False):
    try:
        list_available = available.split('|')
        # Obtener los recursos del tipo de contenido
        if not resp:
            resources = mongodb.get_record('resources', {'post_type': {
                                           '$in': list_available}, 'parents.post_type': {'$in': list_available}, 'parents.id': id})
        else:
            resources = mongodb.get_all_records('resources', {'post_type': {
                                                '$in': list_available}, 'parents.post_type': {'$in': list_available}, 'parents.id': id}, limit=10)

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

# Funcion para obtener los hijos de un recurso en forma de arbol
@cacheHandler.cache.cache(limit=2000)
def get_tree(root, available, user):
    try:
        list_available = available.split('|')

        # Obtener los recursos del tipo de contenido

        fields = {'metadata.firstLevel.title': 1, 'post_type': 1, 'parent': 1}
        if root == 'all':
            # post_type = mongodb.get_record('post_types', {'slug': list_available[-1]})
            # if not post_type:
            #     return {'msg': 'Tipo de post no existe'}, 404
            # parents = get_type_parents(post_type)
            # parents = [p['slug'] for p in parents]
            # parents = [*parents, post_type['slug']]
            
            resources = list(mongodb.get_all_records('resources', {
                             'post_type': {
                             "$in": list_available}, 'parent': None, 'status': 'published'}, sort=[('metadata.firstLevel.title', 1)], fields=fields))
        else:
            resources = list(mongodb.get_all_records('resources', {'post_type': {
                             "$in": list_available}, 'parent.id': root, 'status': 'published'}, sort=[('metadata.firstLevel.title', 1)], fields=fields))
        
        # Obtener el icono del post type
        # icon = mongodb.get_record(
            # 'post_types', {'slug': list_available[-1]})['icon']
        # Devolver solo los campos necesarios

        resources = [{'name': re['metadata']['firstLevel']['title'], 'post_type': re['post_type'], 'id': str(
            re['_id'])} for re in resources]

        for resource in resources:
            resource['children'] = get_children(resource['id'], available)
            resource['icon'] = get_icon(resource['post_type'])

        # Retornar los recursos y los padres
        return resources, 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Funcion para validar que el tipo del padre sea uno admitido por el hijo
@cacheHandler.cache.cache(limit=1000)
def has_parent_postType(post_type, compare):
    try:
        # Obtener el tipo de post
        post_type = mongodb.get_record('post_types', {'slug': post_type})
        # Si el tipo de post no existe, retornar error
        if not post_type:
            return {'msg': 'Tipo de post no existe'}, 404
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
def get_parents(id):
    try:
        # Buscar el recurso en la base de datos
        resource = mongodb.get_record('resources', {'_id': ObjectId(id)})
        # Si el recurso no existe, retornar error
        if not resource:
            return []
        # Si el recurso no tiene padre, retornar una lista vacia
        if 'parents' not in resource:
            return []
        else:
            if resource['parents']:
                # Obtener los padres del recurso
                parents = [{'post_type': item['post_type'], 'id': item['id']}
                           for item in resource['parents']]
                if 'parents' in resource:
                    parents = [*resource['parents']]
                # Retornar los padres
                return parents
            else:
                return []
    except Exception as e:
        raise Exception(str(e))

# Funcion para obtener el padre directo de un recurso
@cacheHandler.cache.cache(limit=1000)
def get_parent(id):
    try:
        # Buscar el recurso en la base de datos
        resource = mongodb.get_record('resources', {'_id': ObjectId(id)})
        # Si el recurso no existe, retornar error
        if not resource:
            return {'msg': 'Recurso no existe'}, 404
        # Si el recurso no tiene padre, retornar una lista vacia
        if 'parent' not in resource:
            return None
        else:
            if resource['parent']:
                parent = resource['parent']
                return parent['id']
            else:
                return []
    except Exception as e:
        raise Exception(str(e))

# Funcion para determinar si un recurso cambio de padre
def has_changed_parent(id, body):
    try:
        if (len(body['parents']) == 0):
            return False
        # Obtener los padres del recurso
        parent = get_parent(id)
        if parent != body['parents'][0]['id']:
            return True
        else:
            return False
    except Exception as e:
        raise Exception(str(e))

# Funcion para obtener los hijos directos de un recurso
def get_direct_children(id):
    try:
        # Buscar los recursos en la base de datos con parent igual al id
        resources = list(mongodb.get_all_records(
            'resources', {'parent.id': id}))

        resources = [{'post_type': re['post_type'],
                      'id': str(re['_id'])} for re in resources]

        return resources

    except Exception as e:
        raise Exception(str(e))

# Funcion para actualizar los padres recursivamente


def update_parents(id, post_type):
    try:
        get_parents.invalidate_all()
        get_parent.invalidate_all()
        # Hijos directos del recurso
        children = get_direct_children(id)
        # Si el recurso tiene hijos directos, actualizar el parent de cada hijo
        if children:
            for child in children:
                parents = [{
                    'post_type': post_type,
                    'id': id
                }]
                parents = [*parents, *get_parents(id)]
                parent = {
                    'post_type': post_type,
                    'id': id
                }
                updata = ResourceUpdate(
                    **{'parents': parents, 'parent': parent})
                mongodb.update_record(
                    'resources', {'_id': ObjectId(child['id'])}, updata)
                update_parents(child['id'], child['post_type'])

    except Exception as e:
        raise Exception(str(e))

# Funcion para actualizar los padres recursivamente de los records


def update_records_parents(id, user):
    try:
        # Hijos directos del recurso
        children = mongodb.get_record('resources', {'_id': ObjectId(id)})
        children = children['files']
        # Si el recurso tiene hijos directos, actualizar el parent de cada hijo
        if children:
            for child in children:
                record = mongodb.get_record(
                    'records', {'_id': ObjectId(child)})
                parents = record['parent']
                temp = []

                for p in parents:
                    temp = [*temp, *get_parents(p['id'])]

                update_parent(child, user, temp)

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
            raise Exception('Recurso no existe')
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
    clear_cache()