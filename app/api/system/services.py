from flask import jsonify, request
from app.utils import DatabaseHandler
from bson import json_util
from functools import lru_cache
import json
import re
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
        keys = path.split('.')
        value = dict
        for key in keys:
            if key in value:
                value = value.get(key)
            else:
                value = None
                break

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
    
# Funcion para validar un array de textos
def validate_text_array(value, field):
    try:
        label = field['label']
        # Si el valor no es de tipo array, retornar error
        if not isinstance(value, list):
            raise Exception(f'El campo {label} debe ser de tipo array')
        # Si field.required entonces el valor no puede ser vacío o == []
        if field['required'] and (value == [] or value == None):
            raise Exception(f'El campo {label} es requerido')
        # Si el campo tiene min_items, validar que el array tenga al menos min_items items
        if 'min_items' in field and len(value) < field['min_items']:
            raise Exception(f'El campo {label} debe tener al menos {field["min_items"]} items')
        # Si el campo tiene max_items, validar que el array tenga como máximo max_items items
        if 'max_items' in field and len(value) > field['max_items']:
            raise Exception(f'El campo {label} debe tener como máximo {field["max_items"]} items')
        # Si el campo tiene items, validar que todos los items del array sean de tipo string
        if 'items' in field:
            for item in value:
                if not isinstance(item, str):
                    raise Exception(f'El campo {label} debe ser de tipo string')
        return value
    except Exception as e:
        raise Exception(f'Error al validar el campo {label}')
    
# Funcion para validar un text de acuerdo a un regex
def validate_text_regex(value, field):
    print(value,field)
    try:
        label = field['label']
        # Si el valor no es de tipo string, retornar error
        if not isinstance(value, str):
            raise Exception(f'El campo {label} debe ser de tipo string')
        # Si field.required entonces el valor no puede ser vacío o == ''
        if field['required'] and (value == '' or value == None):
            raise Exception(f'El campo {label} es requerido')
        

        # Si el campo tiene regex, validar que el valor cumpla con el regex
        if 'pattern' in field:
            print("1", field['pattern'])
            
            # regex para validar una url
            regex = field['pattern']

            # si el pattern es url, validar que el valor sea una url
            if not re.match(regex, value):

                raise Exception(f'El campo {label} debe ser una url')
        return value
    except Exception as e:
        print(str(e))
        raise Exception(f'Error al validar el campo {label}')