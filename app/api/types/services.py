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
from app.api.forms.services import get_by_slug as get_form_by_slug

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para obtener todos los tipos de 
@lru_cache(maxsize=1)
def get_all():
    try:
        # Obtener todos los tipos de post en orden alfabetico ascendente por el campo name
        post_types = mongodb.get_all_records('post_types', {}, [('name', 1)])
        # Quitar todos los campos menos el nombre y la descripción
        post_types = [{ 'name': post_type['name'], 'description': post_type['description'], 'slug': post_type['slug']} for post_type in post_types]
        # Retornar post_types
        return jsonify(post_types), 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para crear un tipo de post
def create(body, user):
    try:
        # Crear instancia de PostType con el body del request
        post_type = PostType(**body)
        # Insertar el tipo de post en la base de datos
        new_post_type = mongodb.insert_record('post_types', post_type)
        # Registrar el log
        register_log(user, log_actions['type_create'], {'post_type': {
            'name': post_type.name,
            'slug': post_type.slug,
        }})
        # Limpiar la cache
        get_all.cache_clear()
        get_by_slug.cache_clear()
        # Retornar el resultado
        return {'msg': 'Tipo de post creado exitosamente'}, 201
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para obtener un tipo de post por su slug
@lru_cache(maxsize=30)
def get_by_slug(slug):
    try:
        # Buscar el tipo de post en la base de datos
        post_type = mongodb.get_record('post_types', {'slug': slug})
        # Si el tipo de post no existe, retornar error
        if not post_type:
            return {'msg': 'Tipo de post no existe'}
        # quitamos el id del tipo de post
        post_type.pop('_id')
        # Parsear el resultado
        post_type = parse_result(post_type)
        # Obtener los padres del tipo de post
        parents = get_parents(post_type)
        # Agregar los padres al tipo de post
        post_type['parents'] = parents
        # Si el campo metadata es un string y es distinto a '', recuperar el formulario con ese slug
        if type(post_type['metadata']) == str and post_type['metadata'] != '':
            post_type['metadata'] = get_form_by_slug(post_type['metadata'])
            # dejar solo los campos name y slug del formulario
            post_type['metadata'] = { 'name': post_type['metadata']['name'], 'fields': post_type['metadata']['fields'] }
        else:
            post_type['metadata'] = None
        # Retornar el resultado
        return post_type
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para actualizar un tipo de post
def update_by_slug(slug, body, user):
    # Buscar el tipo de post en la base de datos
    post_type = mongodb.get_record('post_types', {'slug': slug})
    try:
        # crear instancia de PostTypeUpdate con el body del request
        post_type_update = PostTypeUpdate(**body)
        # Si el tipo de post no existe, retornar error
        if not post_type:
            return {'msg': 'Tipo de post no existe'}, 404
        # Actualizar el tipo de post
        mongodb.update_record('post_types', {'slug': slug}, post_type_update)
        # Registrar el log
        register_log(user, log_actions['type_update'], {'post_type': body})
        # Limpiar la cache
        get_all.cache_clear()
        get_by_slug.cache_clear()
        # Retornar el resultado
        return {'msg': 'Tipo de post actualizado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para eliminar un tipo de post
def delete_by_slug(slug, user):
    # Buscar el tipo de post en la base de datos
    post_type = mongodb.get_record('post_types', {'slug': slug})
    # Si el tipo de post no existe, retornar error
    if not post_type:
        return {'msg': 'Tipo de post no existe'}, 404
    # Eliminar el tipo de post
    mongodb.delete_record('post_types', {'slug': slug})
    # Registrar el log
    register_log(user, log_actions['type_delete'], {'post_type': {
        'name': post_type['name'],
        'slug': post_type['slug'],
    }})
    # Limpiar la cache
    get_all.cache_clear()
    get_by_slug.cache_clear()
    # Retornar el resultado
    return {'msg': 'Tipo de post eliminado exitosamente'}, 200

# Funcion que devuelve recursivamente los padres de un tipo de post
def get_parents(post_type):
    # Si el tipo de post no tiene padre, retornar una lista vacia
    if post_type['parentType'] == '':
        return []
    # Buscar el padre del tipo de post
    parent = mongodb.get_record('post_types', {'slug': post_type['parentType']})
    # Si el padre no existe, retornar una lista vacia
    if not parent:
        return []
    # Retornar el padre y los padres del padre
    return [{
        'name': parent['name'],
        'slug': parent['slug'],
    }] + get_parents(parent)

# Funcion para agregar al contador de recursos de un tipo de post
def add_resource(post_type_slug):
    # Buscar el tipo de post en la base de datos
    post_type = mongodb.get_record('post_types', {'slug': post_type_slug})
    # Si el tipo de post no existe, retornar error
    if not post_type:
        return {'msg': 'Tipo de post no existe'}, 404
    # Incrementar el contador de recursos del tipo de post
    mongodb.update_record('post_types', {'slug': post_type_slug}, {'$inc': {'resources': 1}})
    # Retornar el resultado
    return {'msg': 'Contador de recursos incrementado exitosamente'}, 200