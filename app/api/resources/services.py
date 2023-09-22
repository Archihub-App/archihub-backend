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
from app.api.types.services import add_resource
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
from werkzeug.utils import secure_filename
from app.api.records.services import create as create_record
import os

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para obtener todos los recursos dado un tipo de contenido
def get_all(post_type, body, user):
    try:
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
        resources = list(mongodb.get_all_records('resources', filters, limit=limit, skip=skip))
        # Obtener el total de recursos dado un tipo de contenido
        total = get_total(json.dumps(filters))
        # Para cada recurso, obtener el formulario asociado y quitar los campos _id
        for resource in resources:
            resource['id'] = str(resource['_id'])
            resource.pop('_id')
            resource['total'] = total
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
        # Obtener los metadatos en función del tipo de contenido
        metadata = get_metadata(body['post_type'])

        errors = {}
        # Validar los campos de la metadata
        validate_fields(body, metadata, errors)

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
        # crear el record
        records = create_record(body['_id'], user, files)
        
        update = {
            'files': records
        }

        update_ = ResourceUpdate(**update)

        mongodb.update_record('resources', {'_id': ObjectId(body['_id'])}, update_)

        # limpiar la cache
        has_parent_postType.cache_clear()
        get_tree.cache_clear()
        get_children.cache_clear()
        get_resource.cache_clear()

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
                raise Exception('El recurso no tiene como padre al recurso padre')
            
            body['parents'] = [parent, *get_parents(parent['id'])]
            body['parent'] = parent
            return body
        else:
            if hierarchical[0] and hierarchical[1]:
                raise Exception('El tipo de contenido es jerarquico y debe tener padre')
            elif hierarchical[0] and not hierarchical[1]:
                body['parents'] = []
                body['parent'] = None
                return body
            elif not hierarchical[0] and hierarchical[1]:
                raise Exception('El tipo de contenido debe tener un padre')
    
# Funcion para validar los campos de la metadata
def validate_fields(body, metadata, errors):
    for field in metadata['fields']:
        try:
            if field['type'] != 'file' and field['type'] != 'separator':
                if field['destiny'] != 'ident':
                    if field['type'] == 'text':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_text(get_value_by_path(body, field['destiny']), field)
                        elif field['required']:
                            errors[field['destiny']] = f'El campo {field["label"]} es requerido'
                    if field['type'] == 'textarea':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_text(get_value_by_path(body, field['destiny']), field)
                        elif field['required']:
                            errors[field['destiny']] = f'El campo {field["label"]} es requerido'
                    if field['type'] == 'select':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_text(get_value_by_path(body, field['destiny']), field)
                        elif field['required']:
                            errors[field['destiny']] = f'El campo {field["label"]} es requerido'
                    if field['type'] == 'pattern':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_text_regex(get_value_by_path(body, field['destiny']), field)
                        elif field['required']:
                            errors[field['destiny']] = f'El campo {field["label"]} es requerido'
                    if field['type'] == 'select-multiple2':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_text_array(get_value_by_path(body, field['destiny']), field)
                        elif field['required']:
                            errors[field['destiny']] = f'El campo {field["label"]} es requerido'
                    if field['type'] == 'author':
                        exists = get_value_by_path(body, field['destiny'])
                        if exists:
                            validate_author_array(get_value_by_path(body, field['destiny']), field)
                        elif field['required']:
                            errors[field['destiny']] = f'El campo {field["label"]} es requerido'
                    # if field['type'] == 'simple-date':
                    #     exists = get_value_by_path(body, field['destiny'])
                    #     if exists:
                    #         print(get_value_by_path(body, field['destiny']))
                    #         # raise Exception('Error')
                    #     elif field['required']:
                    #         errors[field['destiny']] = f'El campo {field["label"]} es requerido'

        except Exception as e:
            errors[field['destiny']] = str(e)
    
# Nuevo servicio para obtener un recurso por su id
def get_by_id(id, user):
    try:
        resource = get_resource(id)
        register_log(user, log_actions['resource_open'], {'resource': id})

        # Retornar el recurso
        return jsonify(resource), 200
    except Exception as e:
        return {'msg': str(e)}, 500

@lru_cache(maxsize=1000)
def get_resource(id):
    # Buscar el recurso en la base de datos
    resource = mongodb.get_record('resources', {'_id': ObjectId(id)})
    # Si el recurso no existe, retornar error
    if not resource:
        return {'msg': 'Recurso no existe'}, 404
    # Registrar el log
    resource['_id'] = str(resource['_id'])

    if 'parents' in resource:
        if resource['parents']:
            for r in resource['parents']:
                r_ = mongodb.get_record('resources', {'_id': ObjectId(r['id'])})
                r['name'] = r_['metadata']['firstLevel']['title']
                r['icon'] = get_icon(r_['post_type'])
    
    resource['icon'] = get_icon(resource['post_type'])

    default_visible_type = get_default_visible_type()
    resource['children'] = mongodb.distinct('resources', 'post_type', {'parents.id': id, 'post_type': {'$in': default_visible_type['value']}})

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

            r_ = get_resource_records(json.dumps(ids))
            for _ in r_:
                temp.append({
                    'name': _['name'],
                    'size': _['size'],
                    'id': str(_['_id'])
                })

            resource['files'] = temp

    resource['fields'] = get_metadata(resource['post_type'])['fields']

    temp = []
    for f in resource['fields']:
        if f['type'] != 'file' and f['type'] != 'separator':
            if f['type'] == 'text' or f['type'] == 'textarea':
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
                if value:
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

    return resource

def get_resource_records(ids):
    ids = json.loads(ids)
    for i in range(len(ids)):
        ids[i] = ObjectId(ids[i])
    try:
        r_ = list(mongodb.get_all_records('records', {'_id': {'$in': ids}}))
        return r_
    except Exception as e:
        print(str(e))
        raise Exception(str(e))

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
        validate_fields(body, metadata, errors)

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
        updated_resource = mongodb.update_record('resources', {'_id': ObjectId(id)}, resource)

        if has_new_parent:
            update_parents(id, body['post_type'])

        records = create_record(id, user, files)

        print(body['deletedFiles'])
        delete_records(body['deletedFiles'], id, user)

        update = {
            'files': [*body['files'], *records]
        }
        update['files'] = list(set(update['files']))

        update_ = ResourceUpdate(**update)

        
        mongodb.update_record('resources', {'_id': ObjectId(body['_id'])}, update_)

        # Registrar el log
        register_log(user, log_actions['resource_update'], {'resource': body})
        # limpiar la cache
        has_parent_postType.cache_clear()
        get_tree.cache_clear()
        get_children.cache_clear()
        get_resource.cache_clear()
        # Retornar el resultado
        return {'msg': 'Recurso actualizado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para eliminar un recurso
def delete_by_id(id, user):
    try:
        # Eliminar el recurso de la base de datos
        deleted_resource = mongodb.delete_record('resources', {'_id': ObjectId(id)})
        # Eliminar los hijos del recurso
        delete_children(id)
        # Registrar el log
        register_log(user, log_actions['resource_delete'], {'resource': id})
        # limpiar la cache
        has_parent_postType.cache_clear()
        get_tree.cache_clear()
        get_children.cache_clear()
        get_resource.cache_clear()

        # Retornar el resultado
        return {'msg': 'Recurso eliminado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Funcion para obtener los hijos de un recurso
@lru_cache(maxsize=1000)
def get_children(id, available, resp = False):
    try:
        list_available = available.split('|')
        # Obtener los recursos del tipo de contenido
        if not resp:
            resources = mongodb.get_record('resources', {'post_type': {'$in': list_available}, 'parents.post_type': {'$in': list_available}, 'parents.id': id})
        else:
            resources = mongodb.get_all_records('resources', {'post_type': {'$in': list_available}, 'parents.post_type': {'$in': list_available}, 'parents.id': id}, limit=10)
        
        if(resources and not resp):
            return True
        elif not resp:
            return False
        
        if not resources:
            return []
        else:
            resources = [{ 'name': re['metadata']['firstLevel']['title'], 'post_type': re['post_type'], 'id': str(re['_id'])} for re in resources]
            return resources
    except Exception as e:
        return {'msg': str(e)}, 500

# Funcion para obtener los hijos de un recurso en forma de arbol
@lru_cache(maxsize=1000)
def get_tree(root, available, user):
    try:
        print(root, available, user)
        list_available = available.split('|')
        # Obtener los recursos del tipo de contenido
        if root == 'all':
            resources = list(mongodb.get_all_records('resources', {'post_type': list_available[-1], 'parent': None}, sort=[('metadata.firstLevel.title', 1)]))
        else:
            resources = list(mongodb.get_all_records('resources', {'post_type': {"$in": list_available},'parent.id': root}, sort=[('metadata.firstLevel.title', 1)]))
        # Obtener el icono del post type
        icon = mongodb.get_record('post_types', {'slug': list_available[-1]})['icon']
        # Devolver solo los campos necesarios
        resources = [{ 'name': re['metadata']['firstLevel']['title'], 'post_type': re['post_type'], 'id': str(re['_id']), 'icon': icon} for re in resources]

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
        print(post_type, compare)
        # Obtener el tipo de post
        post_type = mongodb.get_record('post_types', {'slug': post_type})
        # Si el tipo de post no existe, retornar error
        if not post_type:
            return {'msg': 'Tipo de post no existe'}, 404
        # Si el tipo de post tiene padre, retornar True
        if post_type['parentType'] != '':
            if(post_type['parentType'] == compare):
                return True
            if(post_type['hierarchical'] and post_type['parentType'] != compare):
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
            return {'msg': 'Recurso no existe'}, 404
        # Si el recurso no tiene padre, retornar una lista vacia
        if 'parents' not in resource:
            return []
        else:
            if resource['parents']:
                # Obtener los padres del recurso
                parents = [{'post_type': item['post_type'], 'id': item['id']} for item in resource['parents']]
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
        if(len(body['parents']) == 0):
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
        resources = list(mongodb.get_all_records('resources', {'parent.id': id}))
        
        resources = [{'post_type': re['post_type'], 'id': str(re['_id'])} for re in resources]

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
                updata = ResourceUpdate(**{'parents': parents, 'parent': parent})
                mongodb.update_record('resources', {'_id': ObjectId(child['id'])}, updata)
                update_parents(child['id'], child['post_type'])

    except Exception as e:
        raise Exception(str(e))
    
    # Funcion para eliminar los hijos recursivamente

# Funcion para eliminar los hijos recursivamente
def delete_children(id):
    try:
        # Hijos directos del recurso
        children = get_direct_children(id)
        # Si el recurso tiene hijos directos, eliminar cada hijo
        if children:
            for child in children:
                mongodb.delete_record('resources', {'_id': ObjectId(child['id'])})
                delete_children(child['id'])
    except Exception as e:
        raise Exception(str(e))
    
def delete_records(list, resource_id, user):
    try:
        for l in list:
            delete_parent(resource_id, l, user)
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