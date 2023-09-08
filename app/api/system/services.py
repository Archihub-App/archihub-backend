from flask import jsonify, request
from app.utils import DatabaseHandler
from bson import json_util
from functools import lru_cache
import json
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.api.system.models import Option
from app.api.system.models import OptionUpdate

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para actualizar el registro resources-schema en la colección system
def update_resources_schema(schema):
    try:
        # Obtener el registro resources-schema de la colección system
        resources_schema = mongodb.get_record('system', {'name': 'resources-schema'})
        # Si el registro no existe, crearlo
        if not resources_schema:
            new = Option(**{'name': 'resources-schema', 'data': schema})
            mongodb.insert_record('system', new)
        # Si el registro existe, actualizarlo
        else:
            update = OptionUpdate(**{'data': schema})
            mongodb.update_record('system', {'name': 'resources-schema'}, update)

        # Retornar el resultado
        return {'msg': 'Schema actualizado exitosamente'}
    except Exception as e:
        raise Exception('Error al actualizar el schema de los recursos')
    
# Funcion para obtener el valor de un dict dado un path
def get_value_by_path(dict, path):
    try:
        # Si el path es un string, convertirlo a lista
        if isinstance(path, str):
            path = path.split('.')
        # Si el path es una lista vacía, retornar el dict
        if len(path) == 0:
            return dict
        # Si el path no es una lista, retornar error
        if not isinstance(path, list):
            raise Exception('El path debe ser un string o una lista')
        # Si el path es una lista, obtener el primer elemento
        key = path[0]
        # Si el key no existe en el dict, retornar error
        if key not in dict:
            raise Exception(f'El campo {key} no existe')
        # Si el key existe en el dict, obtener el valor
        value = dict[key]

        # Si el path tiene más de un elemento, llamar recursivamente a la función
        if len(path) > 1:
            return get_value_by_path(value, '.'.join(path[1:]))
        # Si el path tiene un solo elemento, retornar el valor
        else:
            return value
    except Exception as e:
        raise Exception(f'Error al obtener el valor del campo {key}')
    
# Funcion para validar un valor de texto
def validate_text(value, field):
    try:
        label = field['label']
        # Si el valor no es de tipo string, retornar error
        if not isinstance(value, str):
            raise Exception(f'El campo {label} debe ser de tipo string')
        # Si field.required entonces el valor no puede ser vacío o == ''
        if field['required'] and (value == '' or value == None):
            raise Exception(f'El campo {label} es requerido')
        return value
    except Exception as e:
        raise Exception(f'Error al validar el campo {label}')