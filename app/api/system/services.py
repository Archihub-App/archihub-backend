import datetime
from flask import jsonify, request
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from bson import json_util
import json
import re
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.api.system.models import Option
from app.api.system.models import OptionUpdate
from app.api.lists.services import get_by_id
from app.utils.functions import get_access_rights_id, get_roles_id, get_access_rights, get_roles
import os
import importlib
from app.utils import IndexHandler
from app.utils.index.spanish_settings import settings as spanish_settings
from celery import shared_task
from app.api.tasks.services import add_task
from app.api.types.services import get_metadata
from functools import reduce



mongodb = DatabaseHandler.DatabaseHandler()
index_handler = IndexHandler.IndexHandler()
cacheHandler = CacheHandler.CacheHandler()

ELASTIC_INDEX_PREFIX = os.environ.get('ELASTIC_INDEX_PREFIX', '')

# function que recibe un body y una ruta tipo string y cambia el valor en la ruta dejando el resto igual y retornando el body con el valor cambiado. Si el valor no existe, lo crea
def change_value(body, path, value):
    try:
        keys = path.split('.')
        temp = body
        for key in keys:
            if key not in temp:
                temp[key] = {}
            if key == keys[-1]:
                temp[key] = value
            else:
                temp = temp[key]
        return body
    except Exception as e:
        raise Exception(f'Error al cambiar el valor del campo {key}')

def parse_result(result):
    return json.loads(json_util.dumps(result))

# Funcion para obtener todos los recursos de la coleccion system


def get_all_settings():
    try:
        # Obtener todos los recursos de la coleccion system
        resources = mongodb.get_all_records(
            'system', {"name": {"$ne": "active_plugins"}})
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
        update_option('index_management', settings)

        # Registrar log
        register_log(current_user, log_actions['system_update'], {
            'settings': settings
        })
        # Limpiar la cache
        get_default_cataloging_type.invalidate_all()
        get_default_visible_type.invalidate_all()
        get_roles.invalidate_all()
        get_access_rights.invalidate_all()
        get_roles_id.invalidate_all()
        get_access_rights_id.invalidate_all()
        get_resources_schema.invalidate_all()
        clear_cache()

        # Llamar al servicio para obtener todos los ajustes del sistema
        return {'msg': 'Ajustes del sistema actualizados exitosamente'}, 200

    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500

# Funcion para obtener el tipo por defecto del modulo de catalogacion


@cacheHandler.cache.cache()
def get_default_cataloging_type():
    try:
        # Obtener el registro post_types_settings de la colección system
        post_types_settings = mongodb.get_record(
            'system', {'name': 'post_types_settings'})
        # Si el registro no existe, retornar error
        if not post_types_settings:
            return {'msg': 'No existe el tipo por defecto del modulo de catalogacion'}, 404

        for d in post_types_settings['data']:
            if d['id'] == 'tipo_defecto':
                return {'value': d['value']}, 200

    except Exception as e:
        raise Exception(
            'Error al obtener el tipo por defecto del modulo de catalogacion')

# Funcion para obtener el tipo por defecto del modulo de catalogacion


@cacheHandler.cache.cache()
def get_default_visible_type():
    try:
        # Obtener el registro post_types_settings de la colección system
        post_types_settings = mongodb.get_record(
            'system', {'name': 'post_types_settings'})
        # Si el registro no existe, retornar error
        if not post_types_settings:
            raise Exception(
                'No existe el tipo por defecto del modulo de catalogacion')

        for d in post_types_settings['data']:
            if d['id'] == 'tipos_vista_individual':
                return {'value': d['value']}

    except Exception as e:
        raise Exception(
            'Error al obtener el tipo por defecto del modulo de catalogacion')

# Funcion para actualizar el registro resources-schema en la colección system
def update_resources_schema(schema):
    try:
        # Obtener el registro resources-schema de la colección system
        resources_schema = mongodb.get_record(
            'system', {'name': 'resources-schema'})
        # Si el registro no existe, crearlo
        if not resources_schema:
            new = Option(**{'name': 'resources-schema', 'data': schema})
            mongodb.insert_record('system', new)
        # Si el registro existe, actualizarlo
        else:
            update = OptionUpdate(**{'data': schema})
            mongodb.update_record(
                'system', {'name': 'resources-schema'}, update)

        # Retornar el resultado
        return {'msg': 'Schema actualizado exitosamente'}
    except Exception as e:
        raise Exception('Error al actualizar el schema de los recursos')
    
# Funcio para obtener el schema de los recursos
@cacheHandler.cache.cache()
def get_resources_schema():
    try:
        # Obtener el registro resources-schema de la colección system
        resources_schema = mongodb.get_record(
            'system', {'name': 'resources-schema'})
        # Si el registro no existe, retornar error
        if not resources_schema:
            return {'msg': 'No existe el schema de los recursos'}, 404
        # Retornar el resultado
        return {'schema': resources_schema['data']}
    except Exception as e:
        raise Exception('Error al obtener el schema de los recursos')

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
    

def set_value_in_dict(d, path, value):
    keys = path.split('.')
    last_key = keys.pop()
    sub_dict = reduce(lambda d, key: d.setdefault(key, {}), keys, d)
    sub_dict[last_key] = value

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
            raise Exception(
                f'El campo {label} debe tener al menos {field["min_items"]} items')
        # Si el campo tiene max_items, validar que el array tenga como máximo max_items items
        if 'max_items' in field and len(value) > field['max_items']:
            raise Exception(
                f'El campo {label} debe tener como máximo {field["max_items"]} items')
        # Si el campo tiene items, validar que todos los items del array sean de tipo string
        if 'items' in field:
            for item in value:
                if not isinstance(item, str):
                    raise Exception(
                        f'El campo {label} debe ser de tipo string')
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
        if not isinstance(value, datetime.datetime):
            raise Exception(f'El campo {label} debe ser de tipo fecha')
        # Si field.required entonces el valor no puede ser vacío o == ''
        if field['required'] and (value == '' or value == None):
            raise Exception(f'El campo {label} es requerido')
        return value
    except Exception as e:
        raise Exception(f'Error al validar el campo {label}')


def get_plugins():
    try:
        # Obtener el registro active_plugins de la colección system
        active_plugins = mongodb.get_record(
            'system', {'name': 'active_plugins'})
        # Obtener la ruta de la carpeta plugins
        plugins_path = os.path.join(os.path.dirname(
            os.path.abspath(__file__)), '../../plugins')
        # Obtener todas las carpetas en la carpeta ../../plugins
        plugins = os.listdir(plugins_path)

        resp = []
        for plugin in plugins:
            if os.path.isfile(f'{plugins_path}/{plugin}/__init__.py'):
                plugin_module = importlib.import_module(
                    f'app.plugins.{plugin}')
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
        plugins_path = os.path.join(os.path.dirname(
            os.path.abspath(__file__)), '../../plugins')

        temp = []

        for p in body:
            if os.path.isfile(f'{plugins_path}/{p}/__init__.py'):
                temp.append(p)

        update_dict = {'data': temp}
        update_schema = OptionUpdate(**update_dict)
        mongodb.update_record(
            'system', {'name': 'active_plugins'}, update_schema)

        # Retornar el resultado
        return {'msg': 'Plugins instalados exitosamente, favor reiniciar el sistema para que surtan efecto'}, 200
    except Exception as e:
        raise Exception(str(e))


def change_plugin_status(plugin, user):
    try:
        # Obtener el registro active_plugins de la colección system
        active_plugins = mongodb.get_record(
            'system', {'name': 'active_plugins'})
        # Verificar si el plugin existe
        plugins_path = os.path.join(os.path.dirname(
            os.path.abspath(__file__)), '../../plugins')
        # Obtener todas las carpetas en la carpeta ../../plugins
        plugins = os.listdir(plugins_path)
        if plugin not in plugins:
            return {'msg': 'Plugin no existe'}, 404

        if plugin in active_plugins['data']:
            active_plugins['data'].remove(plugin)
        else:
            active_plugins['data'].append(plugin)

        update_dict = {'data': active_plugins['data']}
        update_schema = OptionUpdate(**update_dict)
        mongodb.update_record(
            'system', {'name': 'active_plugins'}, update_schema)

        # Retornar el resultado
        return {'msg': 'Plugin actualizado exitosamente, favor reiniciar el sistema para que surtan efecto'}, 200

    except Exception as e:
        return {'msg': str(e)}, 500

def regenerate_index(user):
    try:
        # Obtener el registro index_management de la colección system
        index_management = mongodb.get_record(
            'system', {'name': 'index_management'})
        # Si el registro no existe, retornar error
        if not index_management:
            return {'msg': 'No existe el registro index_management'}, 404

        if not index_management['data'][0]['value']:
            return {'msg': 'Indexación no está activada'}, 400

        # Obtener el registro resources-schema de la colección system
        resources_schema = mongodb.get_record(
            'system', {'name': 'resources-schema'})

        mapping = transform_dict_to_mapping(resources_schema['data'])
        mapping.pop('file', None)

        mapping['post_type'] = {
            'type': 'keyword'
        }

        mapping['parents'] = {
            'type': 'object',
            'properties': {
                'id': {
                    'type': 'keyword'
                },
                'post_type': {
                    'type': 'keyword'
                }
            }
        }

        mapping['parent'] = {
            'type': 'object',
            'properties': {
                'id': {
                    'type': 'keyword'
                },
                'post_type': {
                    'type': 'keyword'
                }
            }
        }

        mapping['ident'] = {
            'type': 'keyword'
        }

        mapping['status'] = {
            'type': 'keyword'
        }

        mapping = {
            'properties': mapping
        }

        task = regenerate_index_task.delay(mapping, user)
        add_task(task.id, 'system.regenerate_index', user, 'msg')

        # Retornar el resultado
        return {'msg': 'Indexación finalizada exitosamente'}, 200

    except Exception as e:
        return {'msg': str(e)}, 500


def transform_dict_to_mapping(dict_input):
    try:
        mapping = {}
        for key in dict_input:
            if isinstance(dict_input[key], dict):
                if 'type' not in dict_input[key]:
                    mapping[key] = {
                        'properties': transform_dict_to_mapping(dict_input[key])
                    }
                else:
                    mapping[key] = dict_input[key]
                    if mapping[key]['type'] == 'text':
                        mapping[key]['fields'] = {
                            'keyword': {
                                'type': 'keyword',
                                'ignore_above': 256
                            }
                        }
                        mapping[key]['analyzer'] = 'analyzer_spanish'
                    elif mapping[key]['type'] == 'text-area':
                        mapping[key]['type'] = 'text'
                        mapping[key]['analyzer'] = 'analyzer_spanish'
                    elif mapping[key]['type'] == 'simple-date':
                        mapping[key]['type'] = 'date'
            else:
                mapping[key] = dict_input[key]

        return mapping
    except Exception as e:
        raise Exception(str(e))


def index_resources(user):
    try:
        # Obtener el registro index_management de la colección system
        index_management = mongodb.get_record(
            'system', {'name': 'index_management'})
        # Si el registro no existe, retornar error
        if not index_management:
            return {'msg': 'No existe el registro index_management'}, 404

        if not index_management['data'][0]['value']:
            return {'msg': 'Indexación no está activada'}, 400

        task = index_resources_task.delay(user)
        add_task(task.id, 'system.index_resources', user, 'msg')

        # Retornar el resultado
        return {'msg': 'Indexación finalizada exitosamente'}, 200

    except Exception as e:
        return {'msg': str(e)}, 500
        

def clear_cache():
    print('clearing cache')
    from app.utils.functions import clear_cache as update_cache_function
    from app.api.lists.services import update_cache as update_cache_lists
    from app.api.forms.services import update_cache as update_cache_forms
    from app.api.records.services import update_cache as update_cache_records
    from app.api.resources.services import update_cache as update_cache_resources
    from app.api.types.services import update_cache as update_cache_types
    from app.api.users.services import update_cache as update_cache_users

    try:
        update_cache_function()
        update_cache_lists()
        update_cache_forms()
        update_cache_records()
        update_cache_resources()
        update_cache_types()
        update_cache_users()

        return {'msg': 'Cache limpiada exitosamente'}, 200
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500

@shared_task(ignore_result=False, name='system.regenerate_index')
def regenerate_index_task(mapping, user):
    keys = index_handler.get_alias_indexes(ELASTIC_INDEX_PREFIX + '-resources').keys()
    if len(keys) == 1:
        name = list(keys)[0]
        number = name.split('_')[1]
        number = int(number) + 1
        new_name = ELASTIC_INDEX_PREFIX + '-resources_' + str(number)
        index_handler.create_index(new_name, mapping=mapping, settings=spanish_settings)
        index_handler.add_to_alias(ELASTIC_INDEX_PREFIX + '-resources', new_name)
        index_handler.reindex(name, new_name)
        index_handler.remove_from_alias(ELASTIC_INDEX_PREFIX + '-resources', name)
        index_handler.delete_index(name)

        return 'ok'
    else:
        index_handler.start_new_index()
        return 'ok'
    
@shared_task(ignore_result=False, name='system.index_resources')
def index_resources_task(user):
    skip = 0
    resources = list(mongodb.get_all_records('resources', {}, limit=1000, skip=skip))
    index_handler.delete_all_documents(ELASTIC_INDEX_PREFIX + '-resources')

    while len(resources) > 0:
        for resource in resources:
            document = {}
            post_type = resource['post_type']
            fields = get_metadata(post_type)['fields']
            for f in fields:
                # print(f)
                if f['type'] != 'file' and f['type'] != 'simple-date':
                    destiny = f['destiny']
                    if destiny != '':
                        value = get_value_by_path(resource, destiny)
                        if value != None:
                            document = change_value(document, f['destiny'], value)
                elif f['type'] == 'simple-date':
                    destiny = f['destiny']
                    if destiny != '':
                        value = get_value_by_path(resource, destiny)
                        if value != None:
                            value = value.strftime('%Y-%m-%d')
                            change_value(document, f['destiny'], value)

            document['post_type'] = post_type
            document['parents'] = resource['parents']
            document['parent'] = resource['parent']
            document['ident'] = resource['ident']

            r = index_handler.index_document(ELASTIC_INDEX_PREFIX + '-resources', str(resource['_id']), document)
            if r.status_code != 201 and r.status_code != 200:
                raise Exception('Error al indexar el recurso ' + str(resource['_id']))


        skip += 1000
        resources = list(mongodb.get_all_records('resources', {}, limit=1000, skip=skip))

    return 'ok'