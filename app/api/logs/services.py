from flask import jsonify
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from bson import json_util
import json
from app.api.logs.models import Log
from datetime import datetime
from app.utils import LogActions

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Funcion para obtener la fecha actual
def get_current_date():
    return datetime.now()

# Nuevo servicio para registrar un log
def register_log(username, action, metadata=None):
    # Obtener la fecha actual
    date = get_current_date()
    # Crear instancia de Log con el username, la acción y la fecha
    log = Log(username=username, action=action, date=date, metadata=metadata)
    # Insertar log en la base de datos
    mongodb.insert_record('logs', log)
    # Retornar mensaje de éxito
    return jsonify({'msg': 'Log registrado exitosamente'}), 200

# Nuevo servicio para obtener todos los logs de acuerdo a un filtro
def filter(body):
    try:
        # Obtener todos los logs de la coleccion logs
        logs = mongodb.get_all_records('logs', body['filters'], limit=20, sort=[
                                        ('date', -1)], skip=body['page'] * 20, fields={'_id': 0, 'metadata': 0})
        # Si no hay logs, retornar error
        if not logs:
            return {'msg': 'No se encontraron logs'}, 400
        # Obtener el total de logs
        total = get_total(json.dumps(body['filters']))
        # Parsear el resultado
        logs = parse_result(logs)
        # Agregar el total al resultado
        for r in logs:
            r['total'] = total
        # Retornar el resultado
        return logs, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Funcion para obtener el total de recursos
@cacheHandler.cache.cache()
def get_total(obj):
    try:
        # convertir string a dict
        obj = json.loads(obj)
        # Obtener el total de recursos
        total = mongodb.count('logs', obj)
        # Retornar el total
        return total
    except Exception as e:
        raise Exception(str(e))