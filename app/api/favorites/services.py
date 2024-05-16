from flask import jsonify, request
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from bson import json_util
import json
from app.api.favorites.models import Favorite
from app.api.favorites.models import FavoriteUpdate
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from bson.objectid import ObjectId
from app.utils.functions import get_roles, get_access_rights, get_roles_id, get_access_rights_id

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

def update_cache():
    get_by_id.invalidate_all()
    get_all.invalidate_all()

# Nuevo servicio para obtener todos los favoritos
@cacheHandler.cache.cache()
def get_all():
    try:
        # Obtener todos los favoritos
        favorites = mongodb.get_all_records('favorites', {}, [('user_id', 1)])

        # Quitar todos los campos menos el user_id y el standard_id si es que existe
        favorites = [{ 'user_id': favorite['user_id'], 'standard_id': favorite['standard_id'], 'id': str(favorite['_id']) } for favorite in favorites]

        # Retornar favorites
        return favorites, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para crear un favorito
def create(body, user):
    # Crear instancia de Favorite con el body del request
    try:
        favorite = Favorite(**body)
        # Insertar el favorito en la base de datos
        new_favorite = mongodb.insert_record('favorites', favorite)
        # Registrar el log
        register_log(user, log_actions['favorite_create'], {'favorite': {
            'user_id': favorite.user_id,
            'standard_id': favorite.standard_id,
            'id': str(new_favorite.inserted_id),
        }})
        # Limpiar la cache
        update_cache()
        return {'msg': 'Favorito creado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para obtener un favorito por su id
@cacheHandler.cache.cache()
def get_by_id(id):
    try:
        # Obtener el favorito por su id
        favorite = mongodb.get_record_by_id('favorites', id)
        # Retornar el favorito
        return favorite, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para actualizar un favorito por su id
def update_by_id(id, body, user):
    try:
        # Obtener el favorito por su id
        favorite = mongodb.get_record_by_id('favorites', id)
        # Si el favorito no existe, retornar error
        if favorite is None:
            return {'msg': 'Favorito no existe'}, 404
        # Actualizar el favorito
        mongodb.update_record('favorites', id, body)
        # Registrar el log
        register_log(user, log_actions['favorite_update'], {'favorite': {
            'user_id': favorite['user_id'],
            'standard_id': favorite['standard_id'],
            'id': str(favorite['_id']),
        }})
        # Limpiar la cache
        update_cache()
        return {'msg': 'Favorito actualizado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para eliminar un favorito por su id
def delete_by_id(id, user):
    try:
        # Obtener el favorito por su id
        favorite = mongodb.get_record_by_id('favorites', id)
        # Si el favorito no existe, retornar error
        if favorite is None:
            return {'msg': 'Favorito no existe'}, 404
        # Eliminar el favorito
        mongodb.delete_record('favorites', id)
        # Registrar el log
        register_log(user, log_actions['favorite_delete'], {'favorite': {
            'user_id': favorite['user_id'],
            'standard_id': favorite['standard_id'],
            'id': str(favorite['_id']),
        }})
        # Limpiar la cache
        update_cache()
        return {'msg': 'Favorito eliminado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para obtener todos los favoritos de un usuario
@cacheHandler.cache.cache()
def get_by_user_id(user_id):
    try:
        # Obtener todos los favoritos de un usuario
        favorites = mongodb.get_all_records('favorites', {'user_id': user_id}, [('user_id', 1)])
        # Quitar todos los campos menos el user_id y el standard_id si es que existe
        favorites = [{ 'user_id': favorite['user_id'], 'standard_id': favorite['standard_id'], 'id': str(favorite['_id']) } for favorite in favorites]
        # Retornar los favoritos
        return favorites, 200
    except Exception as e:
        return {'msg': str(e)}, 500