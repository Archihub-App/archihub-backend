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
    # Quitar todos los campos menos el nombre y la descripción
    post_types = [{ 'name': post_type['name'], 'description': post_type['description'], 'slug': post_type['slug']} for post_type in post_types]
    # Retornar post_types
    return jsonify(post_types), 200

# Nuevo servicio para crear un tipo de post
def create(body):
    # Crear instancia de PostType con el body del request
    post_type = PostType(**body)
    # Insertar el tipo de post en la base de datos
    new_post_type = mongodb.insert_record('post_types', post_type)
    # Retornar el resultado
    return {'msg': 'Tipo de post creado exitosamente'}, 201

# Nuevo servicio para obtener un tipo de post por su slug
def get_by_slug(slug):
    # Buscar el tipo de post en la base de datos
    post_type = mongodb.get_record('post_types', {'slug': slug})
    # Si el tipo de post no existe, retornar error
    if not post_type:
        return {'msg': 'Tipo de post no existe'}
    # Parsear el resultado
    post_type = parse_result(post_type)
    # Retornar el resultado
    return post_type