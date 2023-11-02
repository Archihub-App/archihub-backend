from flask import jsonify, request
from app.utils import DatabaseHandler
from bson import json_util
import json
from functools import lru_cache
from bson.objectid import ObjectId
from app.api.resources.models import Resource
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.api.resources.models import ResourceUpdate
from app.api.types.services import is_hierarchical
from app.api.types.services import get_icon
from app.api.types.services import get_metadata
from app.api.system.services import validate_text
from app.api.system.services import validate_text_array
from app.api.system.services import validate_text_regex
from app.api.system.services import get_value_by_path
from app.api.system.services import get_default_visible_type
from app.api.system.services import validate_author_array
from app.api.lists.services import get_option_by_id
from app.api.records.services import delete_parent
from app.api.records.services import update_parent
from app.api.records.services import update_record
from app.api.records.services import create as create_record
from app.api.system.services import get_access_rights
from app.api.users.services import has_right, has_role
from app.utils.functions import get_resource_records, cache_type_roles, clear_cache
import os
mongodb = DatabaseHandler.DatabaseHandler()


# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para obtener todos los recursos dado un tipo de contenido
def get_all(post_type, body, user):
    try:
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

        if 'page' in body:
            skip = body['page'] * limit

        # Obtener todos los recursos dado un tipo de contenido
        resources = list(mongodb.get_all_records(
            'resources', filters, limit=limit, skip=skip, fields={'metadata.firstLevel.title': 1, 'accessRights': 1}, sort=[('metadata.firstLevel.title', 1)]))
        # Obtener el total de recursos dado un tipo de contenido
        total = get_total(json.dumps(filters))
        # Para cada recurso, obtener el formulario asociado y quitar los campos _id
        for resource in resources:
            resource['id'] = str(resource['_id'])
            resource.pop('_id')
            resource['total'] = total
            resource['accessRights'] = get_option_by_id(resource['accessRights'])
            if 'term' in resource['accessRights']:
                resource['accessRights'] = resource['accessRights']['term']
            else:
                resource['accessRights'] = None
        # Retornar los recursos
        return jsonify(resources), 200
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
        # Agregar el campo status al body
        body['status'] = 'created'
        print('het')

        # Obtener los metadatos en función del tipo de contenido
        metadata = get_metadata(body['post_type'])

        errors = {}
        # Validar los campos de la metadata
        body = validate_fields(body, metadata, errors)

        # agregamos el ident a la metadata
        body['ident'] = 'ident'

        if errors:
            return {'msg': 'Error al validar los campos', 'errors': errors}, 400

        body['files'] = []
        # Crear instancia de Resource con el body del request
        resource = Resource(**body)

        # Insertar el recurso en la base de datos
        new_resource = mongodb.insert_record('resources', resource)
        body['_id'] = str(new_resource.inserted_id)
        # Registrar el log
        register_log(user, log_actions['resource_create'], {'resource': body})

        if files:
        # crear el record
            try:
                records = create_record(body['_id'], user, files)
            except Exception as e:
                return {'msg': str(e)}, 500

            update = {
                'files': records
            }

            update_ = ResourceUpdate(**update)

            mongodb.update_record(
                'resources', {'_id': ObjectId(body['_id'])}, update_)

        # limpiar la cache
        update_cache()

        # Retornar el resultado
        return {'msg': 'Recurso creado exitosamente'}, 201
    except Exception as e:
        return {'msg': str(e)}, 500

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
                raise Exception(
                    'El tipo de contenido es jerarquico y debe tener padre')
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
                    if field['type'] == 'text':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_text(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required']:
                            errors[field['destiny']
                                   ] = f'El campo {field["label"]} es requerido'
                    if field['type'] == 'text-area':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_text(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required']:
                            errors[field['destiny']
                                   ] = f'El campo {field["label"]} es requerido'
                    if field['type'] == 'select':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_text(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required'] and field['destiny'] != 'accessRights':
                            errors[field['destiny']
                                   ] = f'El campo {field["label"]} es requerido'
                    if field['type'] == 'pattern':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_text_regex(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required']:
                            errors[field['destiny']
                                   ] = f'El campo {field["label"]} es requerido'
                    if field['type'] == 'select-multiple2':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_text_array(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required']:
                            errors[field['destiny']
                                   ] = f'El campo {field["label"]} es requerido'
                    if field['type'] == 'author':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_author_array(get_value_by_path(
                                body, field['destiny']), field)
                        elif field['required']:
                            errors[field['destiny']
                                   ] = f'El campo {field["label"]} es requerido'
                    # if field['type'] == 'simple-date':
                    #     exists = get_value_by_path(body, field['destiny'])
                    #     if exists:
                    #         print(get_value_by_path(body, field['destiny']))
                    #         # raise Exception('Error')
                    #     elif field['required']:
                    #         errors[field['destiny']] = f'El campo {field["label"]} es requerido'

        except Exception as e:
            errors[field['destiny']] = str(e)

    if 'accessRights' not in body:
        body['accessRights'] = None
    else:
        if body['accessRights'] == '':
            errors['accessRights'] = 'El recurso debe tener derechos de acceso'
        
        if body['accessRights'] == 'public':
            body['accessRights'] = None

        access_rights = get_access_rights()['options']
        access_rights = [a['id'] for a in access_rights]

        if body['accessRights'] not in access_rights and body['accessRights'] != None:
            errors['accessRights'] = 'El recurso debe tener derechos de acceso válidos'

    return body

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

@lru_cache(maxsize=1000)
def get_resource_type(id):
    resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'post_type': 1})
    if not resource:
        raise Exception('Recurso no existe')
    return resource['post_type']

@lru_cache(maxsize=1000)
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

@lru_cache(maxsize=1000)
def get_resource(id, user):
    # Buscar el recurso en la base de datos
    resource = mongodb.get_record('resources', {'_id': ObjectId(id)})
    # Si el recurso no existe, retornar error
    if not resource:
        raise Exception('Recurso no existe')
    # Registrar el log
    resource['_id'] = str(resource['_id'])

    if 'parents' in resource:
        if resource['parents']:
            for r in resource['parents']:
                r_ = mongodb.get_record(
                    'resources', {'_id': ObjectId(r['id'])})
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

    if 'files' in resource:
        if len(resource['files']) > 0:
            resource['children'] = [{
                'post_type': 'files',
                'name': 'Archivos',
                'icon': 'archivo',
                'slug': 'files',
            }, *resource['children']]

            temp = []

            ids = []
            for r in resource['files']:
                ids.append(r)

            r_ = get_resource_records(json.dumps(ids), user)
            for _ in r_:
                obj = {
                    'id': str(_['_id']),
                    'hash': _['hash'],
                }

                if 'displayName' in _: obj['displayName'] = _['displayName']
                else : obj['displayName'] = _['name']
                if 'accessRights' in _: obj['accessRights'] = _['accessRights']
                if 'processing' in _: obj['processing'] = _['processing']

                temp.append(obj)

            resource['files'] = temp

    resource['fields'] = get_metadata(resource['post_type'])['fields']

    temp = []
    for f in resource['fields']:
        if f['type'] != 'file' and f['type'] != 'separator':
            if f['type'] == 'text' or f['type'] == 'text-area':
                value = get_value_by_path(resource, f['destiny'])

                if value:
                    temp.append({
                        'label': f['label'],
                        'value': value,
                        'type': f['type']
                    })
            if f['type'] == 'select':
                value = get_value_by_path(resource, f['destiny'])
                value = get_option_by_id(value)
                if value and 'term' in value:
                    temp.append({
                        'label': value['term'],
                        'value': [value['term']],
                        'type': 'select'
                    })
            if f['type'] == 'pattern':
                value = get_value_by_path(resource, f['destiny'])
                if value:
                    temp.append({
                        'label': f['label'],
                        'value': value,
                        'type': 'text'
                    })

            if f['type'] == 'author':
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

            if f['type'] == 'select-multiple2':
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

    resource['fields'] = temp
    resource['accessRights'] = get_option_by_id(resource['accessRights'])
    if 'term' in resource['accessRights']:
        resource['accessRights'] = str(resource['accessRights']['_id'])
    else:
        resource['accessRights'] = None

    return resource

def get_resource_files(id, user):
    try:
        resource = mongodb.get_record('resources', {'_id': ObjectId(id)})
        # Si el recurso no existe, retornar error
        if not resource:
            return {'msg': 'Recurso no existe'}, 404

        temp = []
        for r in resource['files']:
            file = mongodb.get_record('records', {'_id': ObjectId(r)})
            temp.append({
                'name': file['name'],
                'size': file['size'],
                'id': str(file['_id']),
                'url': file['filepath']
            })

        resource['files'] = temp
        # Retornar el recurso
        return jsonify(resource['files']), 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para actualizar un recurso
def update_by_id(id, body, user, files):
    try:
        body = validate_parent(body)
        has_new_parent = has_changed_parent(id, body)
        # Obtener los metadatos en función del tipo de contenido
        metadata = get_metadata(body['post_type'])

        errors = {}

        # Validar los campos de la metadata
        body = validate_fields(body, metadata, errors)

        if errors:
            return {'msg': 'Error al validar los campos', 'errors': errors}, 400

        body['status'] = 'updated'

        temp = []
        for f in body['files']:
            if type(f) == str:
                temp.append(f)

        body['files'] = temp


        # Crear instancia de ResourceUpdate con el body del request
        resource = ResourceUpdate(**body)

        # Actualizar el recurso en la base de datos
        updated_resource = mongodb.update_record(
            'resources', {'_id': ObjectId(id)}, resource)

        if has_new_parent:
            update_parents(id, body['post_type'])
            update_records_parents(id, user)

        try:
            records = create_record(id, user, files)
        except Exception as e:
            print(str(e))
            return {'msg': str(e)}, 500

        delete_records(body['deletedFiles'], id, user)
        update_records(body['updatedFiles'], user)

        update = {
            'files': [*body['files'], *records]
        }
        update['files'] = list(set(update['files']))

        update_ = ResourceUpdate(**update)

        mongodb.update_record(
            'resources', {'_id': ObjectId(body['_id'])}, update_)

        # Registrar el log
        register_log(user, log_actions['resource_update'], {'resource': body})
        # limpiar la cache
        update_cache()
        # Retornar el resultado
        return {'msg': 'Recurso actualizado exitosamente'}, 200
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
        # Eliminar los hijos del recurso
        # Registrar el log
        register_log(user, log_actions['resource_delete'], {'resource': id})
        # limpiar la cache
        update_cache()

        # Retornar el resultado
        return {'msg': 'Recurso eliminado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Funcion para obtener los hijos de un recurso


@lru_cache(maxsize=1000)
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


@lru_cache(maxsize=1000)
def get_tree(root, available, user):
    try:
        list_available = available.split('|')
        # Obtener los recursos del tipo de contenido
        if root == 'all':
            resources = list(mongodb.get_all_records('resources', {
                             'post_type': list_available[-1], 'parent': None}, sort=[('metadata.firstLevel.title', 1)]))
        else:
            resources = list(mongodb.get_all_records('resources', {'post_type': {
                             "$in": list_available}, 'parent.id': root}, sort=[('metadata.firstLevel.title', 1)]))
        # Obtener el icono del post type
        icon = mongodb.get_record(
            'post_types', {'slug': list_available[-1]})['icon']
        # Devolver solo los campos necesarios
        resources = [{'name': re['metadata']['firstLevel']['title'], 'post_type': re['post_type'], 'id': str(
            re['_id']), 'icon': icon} for re in resources]

        for resource in resources:
            resource['children'] = get_children(resource['id'], available)
            resource['icon'] = get_icon(resource['post_type'])
        # Retornar los recursos y los padres
        return resources, 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Funcion para validar que el tipo del padre sea uno admitido por el hijo


@lru_cache(maxsize=1000)
def has_parent_postType(post_type, compare):
    try:
        # Obtener el tipo de post
        post_type = mongodb.get_record('post_types', {'slug': post_type})
        # Si el tipo de post no existe, retornar error
        if not post_type:
            return {'msg': 'Tipo de post no existe'}, 404
        # Si el tipo de post tiene padre, retornar True
        if post_type['parentType'] != '':
            if (post_type['parentType'] == compare):
                return True
            if (post_type['hierarchical'] and post_type['parentType'] != compare):
                return True

        # Si el tipo de post no tiene padre, retornar False
        return False
    except Exception as e:
        return {'msg': str(e)}, 500

# Funcion para obtener los padres de un recurso


@lru_cache(maxsize=1000)
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


@lru_cache(maxsize=1000)
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
        get_parents.cache_clear()
        get_parent.cache_clear()
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
                    print(p)
                    temp = [*temp, *get_parents(p['id'])]
                    print(temp)

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

# Funcion para obtener el total de recursos
@lru_cache(maxsize=500)
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
    get_access_rights.cache_clear()
    get_resource.cache_clear()
    get_children.cache_clear()
    get_tree.cache_clear()
    has_parent_postType.cache_clear()
    get_parents.cache_clear()
    get_parent.cache_clear()
    get_total.cache_clear()
    get_accessRights.cache_clear()
    get_resource_type.cache_clear()
    clear_cache()