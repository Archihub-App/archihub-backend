from flask import jsonify, request
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from bson import json_util
import json
from app.api.snaps.models import Snap
from app.api.snaps.models import SnapUpdate
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

# Nuevo servicio para obtener todos los snaps
@cacheHandler.cache.cache()
def get_all():
    try:
        # Obtener todos los snaps
        snaps = mongodb.get_all_records('snaps', {}, [('name', 1)])

        # Quitar todos los campos menos el nombre y la descripción si es que existe
        snaps = [{ 'name': snap['name'], 'id': str(snap['_id']) } for snap in snaps]

        # Retornar snaps
        return snaps, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para crear un snap
def create(body, user):
    # Crear instancia de Snap con el body del request
    try:
        snap = Snap(**body)
        # Insertar el snap en la base de datos
        new_snap = mongodb.insert_record('snaps', snap)
        # Registrar el log
        register_log(user, log_actions['snap_create'], {'snap': {
            'name': snap.name,
            'id': str(new_snap.inserted_id),
        }})
        # Limpiar la cache
        update_cache()
        return {'msg': 'Snap creado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para obtener un snap por su id
@cacheHandler.cache.cache()
def get_by_id(id):
    try:
        # Obtener el snap por su id
        snap = mongodb.get_record_by_id('snaps', id)
        # Si el snap no existe, retornar error
        if snap is None:
            return {'msg': 'Snap no encontrado'}, 404
        # Retornar el snap
        return parse_result(snap), 200
    except Exception as e:
        return {'msg': str(e)}, 500
    

# Nuevo servicio para actualizar un snap por su id
def update_by_id(id, body, user):
    try:
        # Obtener el snap por su id
        snap = mongodb.get_record_by_id('snaps', id)
        # Si el snap no existe, retornar error
        if snap is None:
            return {'msg': 'Snap no encontrado'}, 404
        # Crear instancia de SnapUpdate con el body del request
        snap_update = SnapUpdate(**body)
        # Actualizar el snap en la base de datos
        mongodb.update_record('snaps', {'_id': ObjectId(id)}, snap_update)
        # Registrar el log
        register_log(user, log_actions['snap_update'], {'snap': {
            'name': snap['name'],
            'id': id,
        }})
        # Limpiar la cache
        update_cache()
        return {'msg': 'Snap actualizado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para eliminar un snap por su id
def delete_by_id(id, user):
    try:
        # Obtener el snap por su id
        snap = mongodb.get_record_by_id('snaps', id)
        # Si el snap no existe, retornar error
        if snap is None:
            return {'msg': 'Snap no encontrado'}, 404
        # Eliminar el snap de la base de datos
        mongodb.delete_record('snaps', id)
        # Registrar el log
        register_log(user, log_actions['snap_delete'], {'snap': {
            'name': snap['name'],
            'id': id,
        }})
        # Limpiar la cache
        update_cache()
        return {'msg': 'Snap eliminado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para obtener todos los snaps de un usuario
@cacheHandler.cache.cache()
def get_by_user_id(user_id):
    try:
        # Obtener todos los snaps de un usuario
        snaps = mongodb.get_all_records('snaps', {'user_id': user_id}, [('name', 1)])

        # Quitar todos los campos menos el nombre y la descripción si es que existe
        snaps = [{ 'name': snap['name'], 'id': str(snap['_id']) } for snap in snaps]

        # Retornar snaps
        return snaps, 200
    except Exception as e:
        return {'msg': str(e)}, 500