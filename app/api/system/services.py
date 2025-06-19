import datetime
from flask import jsonify, request
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from app.utils import VectorDatabaseHandler
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
from celery import shared_task
from app.api.tasks.services import add_task
from app.api.types.services import get_metadata
from functools import reduce
from app.utils import HookHandler
from flask_babel import gettext
from app.api.system.tasks.elasticTasks import index_resources_task, index_resources_delete_task, regenerate_index_task
import threading, time

hookHandler = HookHandler.HookHandler()
mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

def hookHandlerIndex():
    hookHandler.register('resource_create', index_resources_task, queue=101)
    hookHandler.register('resource_update', index_resources_task, queue=101)
    hookHandler.register(
        'resource_delete', index_resources_delete_task, queue=101)
    
def hookHandlerVector():
    hookHandler.register('resource_create', vector_resources_task, queue=102)
    hookHandler.register('resource_update', vector_resources_task, queue=102)
    hookHandler.register(
        'resource_delete', vector_resources_delete_task, queue=102)

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
        raise Exception(gettext(u'Error while changing the value of the field {key}', key=key))

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
        raise Exception(gettext(u'Error while getting the resources: {e}', e=str(e)))


def update_option(name, data):
    options = mongodb.get_record('system', {'name': name})
    for d in options['data']:
        if d['id'] in data:
            d['value'] = data[d['id']]
    update = OptionUpdate(**{'data': options['data']})
    mongodb.update_record('system', {'name': name}, update)


def clear_system_cache():
    get_default_cataloging_type.invalidate_all()
    get_default_visible_type.invalidate_all()
    get_roles.invalidate_all()
    get_access_rights.invalidate_all()
    get_roles_id.invalidate_all()
    get_access_rights_id.invalidate_all()
    get_resources_schema.invalidate_all()
    get_plugins.invalidate_all()
    get_system_settings.invalidate_all()
    get_system_actions.invalidate_all()

# Funcion para actualizar los ajustes del sistema
def update_settings(settings, current_user):
    try:
        update_option('post_types_settings', settings)
        update_option('access_rights', settings)
        update_option('api_activation', settings)
        update_option('index_management', settings)
        update_option('user_management', settings)
        update_option('files_management', settings)

        # Registrar log
        register_log(current_user, log_actions['system_update'], {
            'settings': settings
        })
        # Limpiar la cache
        clear_cache()

        # Llamar al servicio para obtener todos los ajustes del sistema
        return {'msg': gettext('System updates applied successfully')}, 200

    except Exception as e:
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
            return {'msg': gettext(u'There si no default cataloging type')}, 404

        for d in post_types_settings['data']:
            if d['id'] == 'tipo_defecto':
                return {'value': d['value']}, 200

    except Exception as e:
        raise Exception(gettext(u'Error while getting the default cataloging type'))

# Funcion para obtener el tipo por defecto del modulo de catalogacion


@cacheHandler.cache.cache()
def get_default_visible_type():
    try:
        # Obtener el registro post_types_settings de la colección system
        post_types_settings = mongodb.get_record(
            'system', {'name': 'post_types_settings'})
        
        # Si el registro no existe, retornar error
        if not post_types_settings:
            raise Exception(gettext(u'The default type of the cataloging module does not exist'))

        for d in post_types_settings['data']:
            if d['id'] == 'tipos_vista_individual':
                return {'value': d['value']}

    except Exception as e:
        raise Exception(gettext(u'Error while getting the default type of the cataloging module'))

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
        return {'msg': gettext(u'Resources schema updated successfully')}, 200
    except Exception as e:
        print(str(e))
        raise Exception(gettext(u'Error while updating the resources schema'))

# Funcio para obtener el schema de los recursos


@cacheHandler.cache.cache()
def get_resources_schema():
    try:
        # Obtener el registro resources-schema de la colección system
        resources_schema = mongodb.get_record(
            'system', {'name': 'resources-schema'})
        # Si el registro no existe, retornar error
        if not resources_schema:
            return {'msg': gettext('There is no resources schema uploaded')}, 404
        # Retornar el resultado
        return {'schema': resources_schema['data']}
    except Exception as e:
        raise Exception(gettext(u'Error while getting the resources schema'))

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
        raise Exception(gettext(u'Error while getting the value of the field {key}', key=key))


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
            raise Exception(gettext(u'The field {label} must be of type string', label=label))
        # Si field.required entonces el valor no puede ser vacío o == ''
        if field['required'] and (value == '' or value == None):
            raise Exception(gettext(u'The field {label} is required', label=label))
        return value
    except Exception as e:
        raise Exception(gettext(u'Error while validating the field {label}', label=label))

# Funcion para validar el formato de una direccion de correo


def validate_email(value, field):
    try:
        label = field['label']
        # Si el valor no es de tipo string, retornar error
        if not isinstance(value, str):
            raise Exception(gettext(u'The field {label} must be of type string', label=label))
        # Si field.required entonces el valor no puede ser vacío o == ''
        if field['required'] and (value == '' or value == None):
            raise Exception(gettext(u'The field {label} is required', label=label))
        # Si el valor no es un email, retornar error
        if not re.match(r"[^@]+@[^@]+\.[^@]+", value):
            raise Exception(gettext(u'The field {label} must be an email', label=label))
        return value
    except Exception as e:
        raise Exception(gettext(u'Error while validating the field {label}', label=label))

# Funcion para validar un array de textos


def validate_text_array(value, field):
    try:
        label = field['label']
        # Si el valor no es de tipo array, retornar error
        if not isinstance(value, list):
            raise Exception(gettext(u'The field {label} must be of type array', label=label))
        # Si field.required entonces el valor no puede ser vacío o == []
        if field['required'] and (value == [] or value == None):
            raise Exception(gettext(u'The field {label} is required', label=label))
        # Si el campo tiene min_items, validar que el array tenga al menos min_items items
        if 'min_items' in field and len(value) < field['min_items']:
            raise Exception(gettext(u'The field {label} must have at least {min_items} items', label=label, min_items=field['min_items']))
        # Si el campo tiene max_items, validar que el array tenga como máximo max_items items
        if 'max_items' in field and len(value) > field['max_items']:
            raise Exception(gettext(u'The field {label} must have at most {max_items} items', label=label, max_items=field['max_items']))
        # Si el campo tiene items, validar que todos los items del array sean de tipo string
        if 'items' in field:
            for item in value:
                if not isinstance(item, str):
                    raise Exception(gettext(u'The field {label} must be of type string', label=label))
        return value
    except Exception as e:
        raise Exception(gettext(u'Error while validating the field {label}', label=label))

# Funcion para validar un valor de tipo autor


def validate_author_array(value, field):
    try:
        label = field['label']
        # Si el valor no es de tipo array, retornar error
        if not isinstance(value, list):
            raise Exception(gettext(u'The field {label} must be of type array', label=label))
        # Si field.required entonces el valor no puede ser vacío o == []
        if field['required'] and (value == [] or value == None):
            raise Exception(gettext(u'The field {label} is required', label=label))
        for item in value:
            if not isinstance(item, str):
                raise Exception(gettext(u'The field {label} must be of type string', label=label))
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
        raise Exception(gettext(u'Error while validating the field {label}', label=label))

# Funcion para validar un text de acuerdo a un regex


def validate_text_regex(value, field):
    try:
        label = field['label']
        # Si el valor no es de tipo string, retornar error
        if not isinstance(value, str):
            raise Exception(gettext(u'The field {label} must be of type string', label=label))
        # Si field.required entonces el valor no puede ser vacío o == ''
        if field['required'] and (value == '' or value == None):
            raise Exception(gettext(u'The field {label} is required', label=label))

        # Si el campo tiene regex, validar que el valor cumpla con el regex
        if 'pattern' in field:

            # regex para validar una url
            regex = field['pattern']

            # si el pattern es url, validar que el valor sea una url
            if not re.match(regex, value):

                raise Exception(gettext(u'The field {label} must be a valid URL', label=label))
        return value
    except Exception as e:
        print(str(e))
        raise Exception(gettext(u'Error while validating the field {label}', label=label))

# Funcion para validar un valor de fecha


def validate_simple_date(value, field):
    try:
        label = field['label']
        # Si el valor no es de tipo string, retornar error
        if not isinstance(value, datetime.datetime):
            raise Exception(gettext(u'The field {label} must be of type date', label=label))
        # Si field.required entonces el valor no puede ser vacío o == ''
        if field['required'] and (value == '' or value == None):
            raise Exception(gettext(u'The field {label} is required', label=label))
        return value
    except Exception as e:
        raise Exception(gettext(u'Error while validating the field {label}', label=label))

@cacheHandler.cache.cache()
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
        raise Exception(gettext(u'Error while getting the plugins: {e}', e=str(e)))


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
        
        get_plugins.invalidate_all()

        # Retornar el resultado
        return {'msg': gettext('Plugins successfully updated, please restart the system')}, 200
    except Exception as e:
        raise Exception(gettext(u'Error while activating the plugins: {e}', e=str(e)))


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
        
        get_plugins.invalidate_all()

        # Retornar el resultado
        return {'msg': gettext('Plugin successfully updated, please restart the system')}, 200

    except Exception as e:
        return {'msg': str(e)}, 500


def regenerate_index(user):
    try:
        # Obtener el registro index_management de la colección system
        index_management = mongodb.get_record(
            'system', {'name': 'index_management'})
        # Si el registro no existe, retornar error
        if not index_management:
            return {'msg': gettext('The field for the index management doesn\'t exists in the system')}, 404

        if not index_management['data'][0]['value']:
            return {'msg': gettext('Indexing is deactivated')}, 400

        # Obtener el registro resources-schema de la colección system
        resources_schema = mongodb.get_record(
            'system', {'name': 'resources-schema'})

        mapping = transform_dict_to_mapping(resources_schema['data'])
        mapping.pop('file', None)

        mapping['post_type'] = {
            'type': 'text',
            'fields': {
                'keyword': {
                    'type': 'keyword',
                    'ignore_above': 256
                }
            }
        }

        mapping['status'] = {
            'type': 'text',
            'fields': {
                'keyword': {
                    'type': 'keyword',
                    'ignore_above': 256
                }
            }
        }

        mapping['parents'] = {
            'type': 'object',
            'properties': {
                'id': {
                    'type': 'text',
                    'fields': {
                        'keyword': {
                            'type': 'keyword',
                            'ignore_above': 256
                        }
                    }
                },
                'post_type': {
                    'type': 'text',
                    'fields': {
                        'keyword': {
                            'type': 'keyword',
                            'ignore_above': 256
                        }
                    }
                }
            }
        }

        mapping['parent'] = {
            'type': 'object',
            'properties': {
                'id': {
                    'type': 'text',
                    'fields': {
                        'keyword': {
                            'type': 'keyword',
                            'ignore_above': 256
                        }
                    }
                },
                'post_type': {
                    'type': 'text',
                    'fields': {
                        'keyword': {
                            'type': 'keyword',
                            'ignore_above': 256
                        }
                    }
                }
            }
        }

        mapping['ident'] = {
            'type': 'text',
            'fields': {
                'keyword': {
                    'type': 'keyword',
                    'ignore_above': 256
                }
            },
        }
        
        mapping['createdAt'] = {
            'type': 'date'
        }

        mapping['status'] = {
            'type': 'text',
            'fields': {
                'keyword': {
                    'type': 'keyword',
                    'ignore_above': 256
                }
            }
        }

        mapping['files'] = {
            'type': 'integer'
        }

        mapping = {
            'properties': mapping
        }
        
        task = regenerate_index_task.delay(mapping, user)
        add_task(task.id, 'system.regenerate_index', user, 'msg')

        # Retornar el resultado
        return {'msg': gettext('The process has been added to the processing queue')}, 200

    except Exception as e:
        return {'msg': gettext(u'Error: {e}', e=str(e))}, 500

def transform_dict_to_mapping(input_dict):
    def map_field(field_def):
        if not isinstance(field_def, dict):
            raise ValueError(gettext(u'Invalid field definition: {field_def}', field_def=field_def))

        if 'type' in field_def and isinstance(field_def['type'], str):
            field_type = field_def['type']

            if field_type == 'text':
                return {
                    'type': 'text',
                    'analyzer': 'analyzer_spanish',
                    'fields': {
                        'keyword': {
                            'type': 'keyword',
                            'ignore_above': 256
                        }
                    }
                }
            elif field_type == 'text-area':
                return {
                    'type': 'text',
                    'analyzer': 'analyzer_spanish'
                }
            elif field_type == 'select':
                return {
                    'type': 'keyword'
                }
            elif field_type == 'simple-date':
                return {
                    'type': 'date'
                }
            elif field_type == 'location':
                return {
                    'type': 'geo_shape'
                }
            elif field_type == 'relation':
                return {
                    'type': 'object',
                    'properties': {
                        'id': {
                            'type': 'text',
                            'fields': {
                                'keyword': {
                                    'type': 'keyword',
                                    'ignore_above': 256
                                }
                            }
                        },
                        'post_type': {
                            'type': 'text',
                            'fields': {
                                'keyword': {
                                    'type': 'keyword',
                                    'ignore_above': 256
                                }
                            }
                        }
                    }
                }
            elif field_type == 'repeater':
                return {
                    'type': 'object'
                }
            elif field_type == 'checkbox':
                return {
                    'type': 'boolean'
                }
            elif field_type == 'number':
                return {
                    'type': 'float'
                }
            else:
                return {'type': field_type}

        else:
            return {
                'type': 'object',
                'properties': {k: map_field(v) for k, v in field_def.items()}
            }

    mapping = {}
    for key, value in input_dict.items():
        mapping[key] = map_field(value)

    return mapping



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

        task = index_resources_task.delay()
        add_task(task.id, 'system.index_resources', user, 'msg')

        # Retornar el resultado
        return {'msg': 'Se ha agregado la tarea de indexación de todo el contenido a la fila de procesos'}, 200

    except Exception as e:
        return {'msg': str(e)}, 500
    

def regenerate_index_geometries(user):
    from app.api.geosystem.services import regenerate_index_shapes
    task = regenerate_index_shapes.delay()
    add_task(task.id, 'geosystem.regenerate_index_shapes', user, 'msg')
    return {'msg': 'Regeneración de geometrías iniciada'}, 200

def index_geometries(user):
    from app.api.geosystem.services import index_shapes
    task = index_shapes.delay()
    add_task(task.id, 'geosystem.index_shapes', user, 'msg')
    return {'msg': 'Indexación de geometrías iniciada'}, 200
    
    
def set_system_setting():
    try:
        from app.api.system.default_settings import settings
        for setting in settings:
            setting_db = mongodb.get_record('system', {'name': setting['name']})
            if not setting_db:
                new = Option(**setting)
                mongodb.insert_record('system', new)
            else:
                for d in setting['data']:
                    if d['id'] not in [s['id'] for s in setting_db['data']]:
                        setting_db['data'].append(d)
                update = OptionUpdate(**{'data': setting_db['data']})
                mongodb.update_record('system', {'name': setting['name']}, update)

    except Exception as e:
        return {'msg': str(e)}, 500

def restart_system():
    def shutdown():
        time.sleep(1)
        import signal
        os.kill(1, signal.SIGTERM)
    threading.Thread(target=shutdown).start()
    return {'msg': gettext('System restarted successfully')}, 200    

@cacheHandler.cache.cache()
def get_system_settings():
    user_management = mongodb.get_record('system', {'name': 'user_management'})
    language = user_management['data'][2]['value']
    from app.version import __version__
    version = __version__
    
    plugins = mongodb.get_record('system', {'name': 'active_plugins'})
    capabilities = []
    for p in plugins['data']:
        plugin_module = __import__(f'app.plugins.{p}', fromlist=[
                               'ExtendedPluginClass', 'plugin_info'])
        
        plugin_info = plugin_module.plugin_info.copy()
            
        plugin_bp = plugin_module.ExtendedPluginClass(
            p, __name__, **plugin_info)
        
        c = plugin_bp.get_capabilities()
        if c:
            capabilities = [*capabilities, *c]
            
    indexing = False
    vector_db = False
    index_management = mongodb.get_record('system', {'name': 'index_management'})
    for d in index_management['data']:
        if d['id'] == 'index_activation':
            indexing = d['value'] if d['value'] else False
        elif d['id'] == 'vector_activation':
            vector_db = d['value'] if d['value'] else False
            
    files_download = False
    files_management = mongodb.get_record('system', {'name': 'files_management'})
    for d in files_management['data']:
        if d['id'] == 'files_download':
            files_download = d['value'] if d['value'] else False
            
    from app.api.llms.services import get_llm_models
    llm_models, status = get_llm_models()
    
    if len(llm_models) > 0:
        capabilities.append('llm')
    if indexing:
        capabilities.append('indexing')
    if files_download:
        capabilities.append('files_download')
    if vector_db:
        capabilities.append('vector_db')
    
    return {
        'language': language,
        'capabilities': capabilities,
        'version': version,
    }, 200
    
@cacheHandler.cache.cache()
def get_system_actions(placement):
    try:
        plugins = mongodb.get_record('system', {'name': 'active_plugins'})
        actions = []
        for p in plugins['data']:
            plugin_module = __import__(f'app.plugins.{p}', fromlist=['ExtendedPluginClass', 'plugin_info'])
            
            plugin_info = plugin_module.plugin_info.copy()
                
            plugin_bp = plugin_module.ExtendedPluginClass(
                p, __name__, **plugin_info)
            
            a = plugin_bp.get_actions()
            if a:
                for _a in a:
                    if _a['placement'] == placement:
                        _a['plugin'] = p
                        actions.append(_a)
                
        return {
            'actions': actions
        }, 200
        
    except Exception as e:
        return {'msg': str(e)}, 500


def clear_cache():
    print('-'*50)
    print('🧹 ♻️  💾 Clearing cache')
    from app.utils.functions import clear_cache as update_cache_function
    from app.api.lists.services import update_cache as update_cache_lists
    from app.api.forms.services import update_cache as update_cache_forms
    from app.api.records.services import update_cache as update_cache_records
    from app.api.resources.services import update_cache as update_cache_resources
    from app.api.types.services import update_cache as update_cache_types
    from app.api.users.services import update_cache as update_cache_users
    from app.api.snaps.services import update_cache as update_cache_snaps
    from app.api.views.services import update_cache as update_cache_views
    from app.api.geosystem.services import update_cache as update_cache_geosystem
    from app.api.llms.services import update_cache as update_cache_llms
    from app.api.resources.public_services import update_cache as update_cache_resources_public
    from app.api.records.public_services import update_cache as update_cache_records_public

    try:
        clear_system_cache()
        update_cache_function()
        update_cache_lists()
        update_cache_forms()
        update_cache_records()
        update_cache_resources()
        update_cache_types()
        update_cache_users()
        update_cache_snaps()
        update_cache_views()
        update_cache_geosystem()
        update_cache_resources_public()
        update_cache_records_public()
        update_cache_llms()

        print('-'*50)
        return {'msg': gettext('Cache cleaned successfully')}, 200
    except Exception as e:
        print(str(e))
        print('-'*50)
        return {'msg': str(e)}, 500



