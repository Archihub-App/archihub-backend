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
        print(post_type, body, user)
        filters = {}
        filters['post_type'] = post_type
        if 'parents' in body:
            if body['parents']:
                filters['parents.id'] = body['parents']['id']
        # Obtener todos los recursos dado un tipo de contenido
        resources = list(mongodb.get_all_records('resources', filters, limit=20, skip=0))
        # Para cada recurso, obtener el formulario asociado y quitar los campos _id
        for resource in resources:
            resource['id'] = str(resource['_id'])
            resource.pop('_id')
        # Retornar los recursos
        return jsonify(resources), 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para crear un recurso
def create(body, user, files):
    try:
        print(body)
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
        
        # Crear instancia de Resource con el body del request
        resource = Resource(**body)
        
        # Insertar el recurso en la base de datos
        new_resource = mongodb.insert_record('resources', resource)
        body['_id'] = str(new_resource.inserted_id)
        # Registrar el log
        register_log(user, log_actions['resource_create'], {'resource': body})
        # agregar el recurso al tipo de contenido
        add_resource(body['post_type'])
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

        # Retornar el resultado
        return {'msg': 'Recurso creado exitosamente'}, 400
    except Exception as e:
        return {'msg': str(e)}, 500
    
def validate_parent(body):
    if 'parents' in body:
        hierarchical = is_hierarchical(body['post_type'])
        if body['parents']:
            parent = body['parents'][0]
            # si el tipo del padre es el mismo que el del hijo y no es jerarquico, retornar error
            if parent['post_type'] == body['post_type'] and not hierarchical[0]:
                raise Exception('El tipo de contenido no es jerarquico')
            # si el tipo del padre es diferente al del hijo y el hijo no lo tiene como padre, retornar error
            elif not has_parent_postType(body['post_type'], parent['post_type']):
                raise Exception('El recurso no tiene como padre al recurso padre')
            
            body['parents'] = [parent, *get_parents(parent['id'])]
            body['parent'] = parent
            return body
        else:
            if hierarchical[0] and hierarchical[1]:
                raise Exception('El tipo de contenido es jerarquico y debe tener padre')
            elif hierarchical[0] and not hierarchical[1]:
                raise Exception('El tipo de contenido debe tener un padre')
            elif not hierarchical[0] and hierarchical[1]:
                raise Exception('El tipo de contenido debe tener un padre')
    

def validate_fields(body, metadata, errors):
    for field in metadata['fields']:
        try:
            if field['type'] != 'file' and field['type'] != 'separator':
                print(field)
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

        except Exception as e:
            errors[field['destiny']] = str(e)
    
# Nuevo servicio para obtener un recurso por su id
def get_by_id(id, user):
    try:
        # Buscar el recurso en la base de datos
        resource = mongodb.get_record('resources', {'_id': ObjectId(id)})
        # Si el recurso no existe, retornar error
        if not resource:
            return {'msg': 'Recurso no existe'}, 404
        # Registrar el log
        register_log(user, log_actions['resource_open'], {'resource': id})
        resource['_id'] = str(resource['_id'])
        # Retornar el recurso
        return jsonify(resource), 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para actualizar un recurso
def update_by_id(id, body, user, files):
    try:
        body = validate_parent(body)

        # Obtener los metadatos en función del tipo de contenido
        metadata = get_metadata(body['post_type'])

        errors = {}
        # Validar los campos de la metadata
        validate_fields(body, metadata, errors)

        if errors:
            return {'msg': 'Error al validar los campos', 'errors': errors}, 400
                
        body['status'] = 'updated'

        # Crear instancia de ResourceUpdate con el body del request
        resource = ResourceUpdate(**body)
        # Actualizar el recurso en la base de datos
        updated_resource = mongodb.update_record('resources', {'_id': ObjectId(id)}, resource)
        # Registrar el log
        register_log(user, log_actions['resource_update'], {'resource': body})
        # limpiar la cache
        has_parent_postType.cache_clear()
        get_tree.cache_clear()
        get_children.cache_clear()
        # Retornar el resultado
        return {'msg': 'Recurso actualizado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para eliminar un recurso
def delete_by_id(id, user):
    try:
        # Eliminar el recurso de la base de datos
        deleted_resource = mongodb.delete_record('resources', {'_id': ObjectId(id)})
        # Registrar el log
        register_log(user, log_actions['resource_delete'], {'resource': id})
        # limpiar la cache
        has_parent_postType.cache_clear()
        get_tree.cache_clear()
        get_children.cache_clear()
        # Retornar el resultado
        return {'msg': 'Recurso eliminado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
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

@lru_cache(maxsize=1000)
def get_tree(root, available, user):
    try:
        list_available = available.split('|')
        # Obtener los recursos del tipo de contenido
        if root == 'all':
            resources = list(mongodb.get_all_records('resources', {'post_type': list_available[-1]}, sort=[('metadata.firstLevel.title', 1)]))
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