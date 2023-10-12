import datetime
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
from app.api.lists.services import get_by_id
import os
import importlib

mongodb = DatabaseHandler.DatabaseHandler()

def parse_result(result):
    return json.loads(json_util.dumps(result))

# Funcion para obtener todos los recursos de la coleccion system
@lru_cache(maxsize=32)
def get_all_settings():
    try:
        # Obtener todos los recursos de la coleccion system
        resources = mongodb.get_all_records('system', {"name": {"$ne": "active_plugins"}})
        # Retornar el resultado
        return {'settings': parse_result(resources)}
    except Exception as e:
        raise Exception('Error al obtener los recursos: ' + str(e))
        
def update_option(name, data):
    options = mongodb.get_record('system', {'name': name})
    for d in options['data']:
        if d['id'] in data:
            d['value'] = data[d['id']]
    update = OptionUpdate(**{'data': options['data']})
    mongodb.update_record('system', {'name': name}, update)
    
# Funcion para actualizar los ajustes del sistema
def update_settings(settings, current_user):
    try:
        update_option('post_types_settings', settings)
        update_option('access_rights', settings)
        update_option('api_activation', settings)

        # Registrar log
        register_log(current_user, log_actions['system_update'], {
            'settings': settings
        })
        # Limpiar la cache
        get_all_settings.cache_clear()
        get_default_cataloging_type.cache_clear()
        get_default_visible_type.cache_clear()
        get_access_rights.cache_clear()
        get_roles.cache_clear()
        # Llamar al servicio para obtener todos los ajustes del sistema
        return {'msg': 'Ajustes del sistema actualizados exitosamente'}, 200
    
    except Exception as e:
        return {'msg': str(e)}, 500

# Funcion para obtener el tipo por defecto del modulo de catalogacion
@lru_cache(maxsize=32)
def get_default_cataloging_type():
    try:
        # Obtener el registro post_types_settings de la colección system
        post_types_settings = mongodb.get_record('system', {'name': 'post_types_settings'})
        # Si el registro no existe, retornar error
        if not post_types_settings:
            return {'msg': 'No existe el tipo por defecto del modulo de catalogacion'}, 404
        
        for d in post_types_settings['data']:
            if d['id'] == 'tipo_defecto':
                return {'value': d['value']}, 200
            
    except Exception as e:
        raise Exception('Error al obtener el tipo por defecto del modulo de catalogacion')
    
# Funcion para obtener el tipo por defecto del modulo de catalogacion
@lru_cache(maxsize=32)
def get_default_visible_type():
    try:
        # Obtener el registro post_types_settings de la colección system
        post_types_settings = mongodb.get_record('system', {'name': 'post_types_settings'})
        # Si el registro no existe, retornar error
        if not post_types_settings:
            raise Exception('No existe el tipo por defecto del modulo de catalogacion')
        
        for d in post_types_settings['data']:
            if d['id'] == 'tipos_vista_individual':
                return {'value': d['value']}
            
    except Exception as e:
        raise Exception('Error al obtener el tipo por defecto del modulo de catalogacion')
    
# Funcion para devolver los access rights
def get_access_rights():
    try:
        # Obtener el registro access_rights de la colección system
        access_rights = mongodb.get_record('system', {'name': 'access_rights'})
        # Si el registro no existe, retornar error
        if not access_rights:
            return {'msg': 'No existe el registro access_rights'}, 404
        
        list_id = access_rights['data'][0]['value']

        # Obtener el listado con list_id
        list = get_by_id(list_id)

        return list, 200
            
    except Exception as e:
        raise Exception('Error al obtener el registro access_rights')
    
# Funcion para devolver los roles
def get_roles():
    try:
        # Obtener el registro access_rights de la colección system
        access_rights = mongodb.get_record('system', {'name': 'access_rights'})
        # Si el registro no existe, retornar error
        if not access_rights:
            return {'msg': 'No existe el registro access_rights'}, 404
        
        roles = access_rights['data'][1]['value']

        # Obtener el listado con roles
        list = get_by_id(roles)

        # clonar list en una variable temporal
        temp = [*list['options']]
        # Agregar admin y editor a la lista
        temp.append({'id': 'admin', 'term': 'admin'})
        temp.append({'id': 'user', 'term': 'user'})
        temp.append({'id': 'editor', 'term': 'editor'})

        return {
            'options': temp
        }, 200
            
    except Exception as e:
        raise Exception('Error al obtener el registro access_rights')

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
    
# Funcion para validar el formato de una direccion de correo
def validate_email(value, field):
    try:
        label = field['label']
        # Si el valor no es de tipo string, retornar error
        if not isinstance(value, str):
            raise Exception(f'El campo {label} debe ser de tipo string')
        # Si field.required entonces el valor no puede ser vacío o == ''
        if field['required'] and (value == '' or value == None):
            raise Exception(f'El campo {label} es requerido')
        # Si el valor no es un email, retornar error
        if not re.match(r"[^@]+@[^@]+\.[^@]+", value):
            raise Exception(f'El campo {label} debe ser un email')
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

# Funcion para validar un valor de tipo autor
def validate_author_array(value, field):
    try:
        label = field['label']
        # Si el valor no es de tipo array, retornar error
        if not isinstance(value, list):
            raise Exception(f'El campo {label} debe ser de tipo array')
        # Si field.required entonces el valor no puede ser vacío o == []
        if field['required'] and (value == [] or value == None):
            raise Exception(f'El campo {label} es requerido')
        for item in value:
            if not isinstance(item, str):
                raise Exception(f'El campo {label} debe ser de tipo string')
            split = item.split(',')
            if len(split) != 2:
                split = item.split('|')
                if len(split) != 2:
                    raise Exception(f'Error {label}')
                else:
                    if split[0] == '' and split[1] == '':
                        raise Exception(f'Error {label}')
            else:
                if split[0] == '' and split[1] == '':
                    raise Exception(f'Error {label}')
                
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

# Funcion para validar un valor de fecha
def validate_simple_date(value, field):
    try:
        label = field['label']
        # Si el valor no es de tipo string, retornar error
        if not isinstance(value, datetime):
            raise Exception(f'El campo {label} debe ser de tipo string')
        # Si field.required entonces el valor no puede ser vacío o == ''
        if field['required'] and (value == '' or value == None):
            raise Exception(f'El campo {label} es requerido')
        return value
    except Exception as e:
        raise Exception(f'Error al validar el campo {label}')
    
def get_plugins():
    try:
        # Obtener el registro active_plugins de la colección system
        active_plugins = mongodb.get_record('system', {'name': 'active_plugins'})
        # Obtener la ruta de la carpeta plugins
        plugins_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../plugins')
        # Obtener todas las carpetas en la carpeta ../../plugins
        plugins = os.listdir(plugins_path)

        resp = []
        for plugin in plugins:
            if os.path.isfile(f'{plugins_path}/{plugin}/__init__.py'):
                plugin_module = importlib.import_module(f'app.plugins.{plugin}')
                plugin_instance = plugin_module.plugin_info
                plugin_instance['slug'] = plugin

                if plugin in active_plugins['data']:
                    plugin_instance['active'] = True
                else:
                    plugin_instance['active'] = False

                resp.append(plugin_instance)

        # Retornar el resultado
        return {'plugins': resp}, 200

    except Exception as e:
        raise Exception(str(e))
    
def activate_plugin(body, current_user):
    try:
        # Obtener la ruta de la carpeta plugins
        plugins_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../plugins')

        temp = []

        for p in body:
            if os.path.isfile(f'{plugins_path}/{p}/__init__.py'):
                temp.append(p)

        update_dict = {'data': temp}
        update_schema = OptionUpdate(**update_dict)
        mongodb.update_record('system', {'name': 'active_plugins'}, update_schema)
            
        # Retornar el resultado
        return {'msg': 'Plugins instalados exitosamente, favor reiniciar el sistema para que surtan efecto'}, 200
    except Exception as e:
        raise Exception(str(e))