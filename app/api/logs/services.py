from flask import jsonify
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from bson import json_util
import json
from app.api.logs.models import Log
from datetime import datetime
from app.utils import LogActions
from flask_babel import _

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
    username = username if username else 'system'
    # Crear instancia de Log con el username, la acción y la fecha
    log = Log(username=username, action=action, date=date, metadata=metadata)
    # Insertar log en la base de datos
    mongodb.insert_record('logs', log)
    # Retornar mensaje de éxito
    return jsonify({'msg': _('Log created successfully')}), 201

# Nuevo servicio para obtener todos los logs de acuerdo a un filtro
def filter(body):
    try:
        # Obtener todos los logs de la coleccion logs
        logs = mongodb.get_all_records('logs', body['filters'], limit=20, sort=[
                                        ('date', -1)], skip=body['page'] * 20, fields={'_id': 0, 'metadata': 0})
        # Si no hay logs, retornar error
        if not logs:
            return {'msg': _('Logs not found')}, 404
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

# Comparar dos objetos y obtener los cambios
def compare_objects(old_obj, new_obj, path_prefix, date):
    changes = []
    
    # Obtener todas las claves de ambos objetos
    all_keys = set(list(old_obj.keys()) + list(new_obj.keys()))
    
    for key in all_keys:
        current_path = f"{path_prefix}.{key}" if path_prefix else key
        
        # Key neuvo (solo está en el nuevo objeto)
        if key not in old_obj:
            changes.append({
                "path": current_path,
                "date": date,
                "old": None,
                "new": new_obj[key]
            })
            continue
            
        # Key eliminado (no está en el nuevo objeto)
        if key not in new_obj:
            changes.append({
                "path": current_path,
                "date": date,
                "old": old_obj[key],
                "new": None
            })
            continue
        
        old_val = old_obj[key]
        new_val = new_obj[key]
        
        if old_val != new_val:
            # Si los valores son diccionarios, comparar recursivamente
            if isinstance(old_val, dict) and isinstance(new_val, dict):
                nested_changes = compare_objects(old_val, new_val, current_path, date)
                changes.extend(nested_changes)
            # Si no son iguales y no son diccionarios, agregar el cambio
            else:
                changes.append({
                    "path": current_path,
                    "date": date,
                    "old": old_val,
                    "new": new_val
                })
    
    return changes

def extract_changes(logs):
    changes = []
    # Revisar que haya al menos dos logs para comparar
    if len(logs) < 2:
        return changes
    
    # Comparar pares de logs consecutivos
    for i in range(len(logs) - 1):
        # Obtener los logs actuales y siguientes
        current_log = logs[i + 1]
        next_log = logs[i]
        
        # Obtener los objetos de los logs
        current_resource = current_log.get('metadata', {}).get('resource', {}).get('metadata', {})
        next_resource = next_log.get('metadata', {}).get('resource', {}).get('metadata', {})
        
        # Comparar los objetos y obtener los cambios con la fecha del siguiente log
        date = next_log['date']['$date']
        detected_changes = compare_objects(current_resource, next_resource, "", date)
        changes.extend(detected_changes)
    
    return changes
    
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
    
# Funcion para cambios en los logs de un recurso
def get_logs(body, resource_id):
    try:
        limit = 20
        skip = 0

        if 'page' in body:
            skip = body['page'] * limit
        # Obtener todos los logs de la coleccion logs
        logs = mongodb.get_all_records('logs',
                                      {'metadata.resource._id': resource_id,
                                       'action': {'$in': ["RESOURCE_CREATE", "RESOURCE_UPDATE"]}},
                                      limit=20,
                                      skip=skip,
                                      sort=[('date', -1)],
                                      fields={'_id': 0})
        
        # Si no hay logs, retornar error
        if not logs:
            return {'msg': _('Logs not found')}, 404
        
        # Parsear el resultado
        logs = parse_result(logs)

        # Extraer los cambios de los logs
        changes = extract_changes(logs)

        # Retornar el resultado
        return changes, 200
    except Exception as e:
        return {'msg': str(e)}, 500