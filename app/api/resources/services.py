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
from  app.api.types.services import is_hierarchical
from  app.api.types.services import get_icon

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para obtener todos los recursos dado un tipo de contenido
def get_all(post_type, body, user):
    try:
        print(post_type, body, user)
        # Obtener todos los recursos dado un tipo de contenido
        resources = list(mongodb.get_all_records('resources', {'post_type': post_type}, limit=20, skip=0))
        # Para cada recurso, obtener el formulario asociado y quitar los campos _id
        for resource in resources:
            resource['id'] = str(resource['_id'])
            resource.pop('_id')
        # Retornar los recursos
        return jsonify(resources), 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para crear un recurso
def create(body, user):
    try:
        print(body)
        # si el body tiene parents, verificar que el recurso sea jerarquico
        if 'parents' in body:
            hierarchical = is_hierarchical(body['post_type'])
            if body['parents']:
                parent = body['parents'][0]
                # si el tipo del padre es el mismo que el del hijo y no es jerarquico, retornar error
                if parent['post_type'] == body['post_type'] and not hierarchical[0]:
                    return {'msg': 'El tipo de contenido no es jerarquico'}, 400
                # si el tipo del padre es diferente al del hijo y el hijo no lo tiene como padre, retornar error
                elif not has_parent_postType(body['post_type'], parent['post_type']):
                    return {'msg': 'El recurso no tiene como padre al recurso padre'}, 400
                
                body['parents'] = [{'type': item['post_type'], 'id': item['id']} for item in body['parents']]
            else:
                if hierarchical[0] and hierarchical[1]:
                    return {'msg': 'El tipo de contenido es jerarquico y no tiene padre'}, 400
                elif hierarchical[0] and not hierarchical[1]:
                    return {'msg': 'El tipo de contenido es jerarquico y no tiene padre'}, 400

        body['status'] = 'created'
        # Crear instancia de Resource con el body del request
        resource = Resource(**body)
        # Insertar el recurso en la base de datos
        new_resource = mongodb.insert_record('resources', resource)
        body['_id'] = str(new_resource.inserted_id)
        # # Registrar el log
        register_log(user, log_actions['resource_create'], {'resource': body})
        # # agregar el recurso al tipo de contenido
        add_resource(body['post_type'])
        # limpiar la cache
        has_parent_postType.cache_clear()
        get_tree.cache_clear()
        get_children.cache_clear()

        # Retornar el resultado
        return {'msg': 'Recurso creado exitosamente'}, 201
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para obtener un recurso por su id
def get_by_id(id, user):
    try:
        # Buscar el recurso en la base de datos
        resource = mongodb.get_record('resources', {'_id': ObjectId(id)})
        # Si el recurso no existe, retornar error
        if not resource:
            return {'msg': 'Recurso no existe'}
        # Registrar el log
        register_log(user, log_actions['resource_read'], {'resource': id})
        # Retornar el recurso
        return jsonify(resource), 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para actualizar un recurso
def update(id, body, user):
    try:
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
            resources = mongodb.get_record('resources', {'post_type': {'$in': list_available}, 'parents.type': {'$in': list_available}, 'parents.id': id})
        else:
            resources = mongodb.get_all_records('resources', {'post_type': {'$in': list_available}, 'parents.type': {'$in': list_available}, 'parents.id': id}, limit=10)
        
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
            resources = list(mongodb.get_all_records('resources', {'post_type': {"$in": list_available},'parents.id': root}, sort=[('metadata.firstLevel.title', 1)]))
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