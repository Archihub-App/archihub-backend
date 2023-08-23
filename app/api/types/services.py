from flask import jsonify
from app.utils import DatabaseHandler
from bson import json_util
import json
from functools import lru_cache
from app.api.types.models import PostType
from app.api.types.models import PostTypeUpdate
from flask import request
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para obtener todos los tipos de 
@lru_cache(maxsize=1)
def get_all():
    # Obtener todos los tipos de post en orden alfabetico ascendente por el campo name
    post_types = mongodb.get_all_records('post_types', {}, [('name', 1)])
    # Quitar todos los campos menos el nombre y la descripción
    post_types = [{ 'name': post_type['name'], 'description': post_type['description'], 'slug': post_type['slug']} for post_type in post_types]
    # Retornar post_types
    return jsonify(post_types), 200

# Nuevo servicio para crear un tipo de post
def create(body, user):
    # Crear instancia de PostType con el body del request
    post_type = PostType(**body)
    # Insertar el tipo de post en la base de datos
    new_post_type = mongodb.insert_record('post_types', post_type)
    # Registrar el log
    register_log(user, log_actions['type_create'])
    # Limpiar la cache
    get_all.cache_clear()
    # Retornar el resultado
    return {'msg': 'Tipo de post creado exitosamente'}, 201

# Nuevo servicio para obtener un tipo de post por su slug
@lru_cache(maxsize=30)
def get_by_slug(slug):
    # Buscar el tipo de post en la base de datos
    post_type = mongodb.get_record('post_types', {'slug': slug})
    # Si el tipo de post no existe, retornar error
    if not post_type:
        return {'msg': 'Tipo de post no existe'}
    # quitamos el id del tipo de post
    post_type.pop('_id')
    # quitamos el slug del tipo de post
    post_type.pop('slug')
    # quitamos el parentType del tipo de post
    post_type.pop('parentType')
    # quitamos el hierarchical del tipo de post
    post_type.pop('hierarchical')
    # quitamos metadata del tipo de post
    post_type.pop('metadata')
    # Parsear el resultado
    post_type = parse_result(post_type)
    # Retornar el resultado
    return post_type

# Nuevo servicio para actualizar un tipo de post
def update_by_slug(slug, body, user):
    # Buscar el tipo de post en la base de datos
    post_type = mongodb.get_record('post_types', {'slug': slug})
    # crear instancia de PostTypeUpdate con el body del request
    post_type_update = PostTypeUpdate(**body)
    # Si el tipo de post no existe, retornar error
    if not post_type:
        return {'msg': 'Tipo de post no existe'}, 404
    # Actualizar el tipo de post
    mongodb.update_record('post_types', {'slug': slug}, post_type_update)
    # Registrar el log
    register_log(user, log_actions['type_update'])
    # Limpiar la cache
    get_all.cache_clear()
    get_by_slug.cache_clear()
    # Retornar el resultado
    return {'msg': 'Tipo de post actualizado exitosamente'}, 200