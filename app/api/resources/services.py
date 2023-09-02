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