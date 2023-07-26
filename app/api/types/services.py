from flask import jsonify
from app.utils import DatabaseHandler
from bson import json_util
import json
from app.api.types.models import PostType
from flask import request

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para obtener todos los tipos de post
def get_all():
    # Obtener todos los tipos de post
    post_types = mongodb.get_all_records('post_types')
    # Parsear el resultado
    post_types = parse_result(post_types)
    # Retornar el resultado
    return jsonify(post_types), 200

# Nuevo servicio para crear un tipo de post
def create(body):
    # Crear instancia de PostType con el body del request
    post_type = PostType(**body)
    # Insertar el tipo de post en la base de datos
    new_post_type = mongodb.insert_record('post_types', post_type)
    # Retornar el resultado
    return parse_result(new_post_type), 200