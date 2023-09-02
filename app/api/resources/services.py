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

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para obtener todos los recursos dado un tipo de contenido
def get_all(post_type, body, user):
    try:
        # Obtener todos los recursos dado un tipo de contenido
        resources = mongodb.get_all_records('resources', {'post_type': post_type}, limit=20, skip=0)
        # Retornar los recursos
        return jsonify(resources), 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para crear un recurso
def create(body, user):
    try:
        print(user,body)
        # Crear instancia de Resource con el body del request
        resource = Resource(**body)
        # Insertar el recurso en la base de datos
        new_resource = mongodb.insert_record('resources', resource)
        # Registrar el log
        register_log(user, log_actions['resource_create'], {'resource': body})
        # agregar el recurso al tipo de contenido
        add_resource(body['post_type'])
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
        # Crear instancia de ResourceUpdate con el body del request
        resource = ResourceUpdate(**body)
        # Actualizar el recurso en la base de datos
        updated_resource = mongodb.update_record('resources', {'_id': ObjectId(id)}, resource)
        # Registrar el log
        register_log(user, log_actions['resource_update'], {'resource': body})
        # Retornar el resultado
        return {'msg': 'Recurso actualizado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para eliminar un recurso
def delete(id, user):
    try:
        # Eliminar el recurso de la base de datos
        deleted_resource = mongodb.delete_record('resources', {'_id': ObjectId(id)})
        # Registrar el log
        register_log(user, log_actions['resource_delete'], {'resource': id})
        # Retornar el resultado
        return {'msg': 'Recurso eliminado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500