from flask import jsonify, request
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from bson import json_util
import json
from app.api.lists.models import List
from app.api.lists.models import ListUpdate
from app.api.lists.models import Option
from app.api.lists.models import OptionUpdate
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from bson.objectid import ObjectId
from app.utils.functions import get_roles, get_access_rights, get_roles_id, get_access_rights_id
from flask_babel import _

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

def update_cache():
    get_by_id.invalidate_all()
    get_all.invalidate_all()

# Nuevo servicio para obtener todos los listados
@cacheHandler.cache.cache()
def get_all():
    try:
        # Obtener todos los listados
        lists = mongodb.get_all_records('lists', {}, [('name', 1)])

        # Quitar todos los campos menos el nombre y la descripción si es que existe
        lists = [{ 'name': lista['name'], 'id': str(lista['_id']) } for lista in lists]

        # Retornar lists
        return lists, 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para crear un estándar de metadatos
def create(body, user):
    # Crear instancia de List con el body del request
    try:
        temp = []
        for option in body['options']:
            option = Option(**option)
            resp = mongodb.insert_record('options', option)
            temp.append(str(resp.inserted_id))

        body['options'] = temp
        lista = List(**body)
        # Insertar el estándar de metadatos en la base de datos
        new_list = mongodb.insert_record('lists', lista)
        # Registrar el log
        register_log(user, log_actions['list_create'], {'list': {
            'name': lista.name,
            'id': str(new_list.inserted_id),
        }})
        # Limpiar la cache
        update_cache()
        # Retornar el resultado
        return {'msg': _('List created successfully')}, 201
    
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para obtener un listado por su slug
@cacheHandler.cache.cache()
def get_by_slug(slug):
    try:
        # Buscar el listado en la base de datos
        lista = mongodb.get_record('lists', {'slug': slug})
        # a lista solo le dejamos los campos name, description, slug y options
        lista = { 'name': lista['name'], 'description': lista['description'], 'options': lista['options'] }
        # Si el listado no existe, retornar error
        if not lista:
            return {'msg': _('List not found')}, 404
        
        opts = []

        records = mongodb.get_all_records('options', {'_id': {'$in': [ObjectId(id) for id in lista['options']]}}, [('term', 1)])
        
        # opts es igual a un arreglo de diccionarios con los campos id y term
        for record in records:
            opts.append({'id': str(record['_id']), 'term': record['term']})

        # agregamos los campos al listado
        lista['options'] = opts
        # Parsear el resultado
        lista = parse_result(lista)

        # Retornar el resultado
        return lista
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para devolver un listado por su id
@cacheHandler.cache.cache()
def get_by_id(id):
    try:
        # Buscar el listado en la base de datos
        lista = mongodb.get_record('lists', {'_id': ObjectId(id)})

        # si description no existe, se agrega un string vacio
        if 'description' not in lista:
            lista['description'] = ''
        # a lista solo le dejamos los campos name, description, y options
        lista = { 'name': lista['name'], 'description': lista['description'], 'options': lista['options'] }
        # Si el listado no existe, retornar error
        if not lista:
            return {'msg': _('List not found')}, 404
        
        opts = []

        records = list(mongodb.get_all_records('options', {'_id': {'$in': [ObjectId(id) for id in lista['options']]}}))
        
        # opts es igual a un arreglo de diccionarios con los campos id y term
        for opt_id in lista['options']:
            for record in records:
                if str(record['_id']) == opt_id:
                    opts.append({'id': str(record['_id']), 'term': record['term']})
                    break

        # agregamos los campos al listado
        lista['options'] = opts
        # Parsear el resultado
        lista = parse_result(lista)

        # Retornar el resultado
        return lista
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para obtener una opcion por su id
@cacheHandler.cache.cache()
def get_option_by_id(id):
    try:
        if not id:
            return None
        # Buscar la opcion en la base de datos
        option = mongodb.get_record('options', {'_id': ObjectId(id)})
        # Si la opcion no existe, retornar error
        if not option:
            return {'msg': _('Option not found')}, 404
        
        # Parsear el resultado
        option = {
            '_id': id,
            'term': option['term'],
        }

        # Retornar el resultado
        return option
    except Exception as e:
        raise Exception(_('Error getting option'))

# Nuevo servicio para actualizar un listado
def update_by_id(id, body, user):
    # Buscar el listado en la base de datos
    lista = mongodb.get_record('lists', {'_id': ObjectId(id)})
    # Si el listado no existe, retornar error
    if not lista:
        return {'msg': _('List not found')}, 404
    # Crear instancia de ListUpdate con el body del request
    try:
        # Actualizar el listado en la base de datos
        # para cada opcion en el body, se convierte el id a ObjectId
        if('options' in body):
            to_delete = []
            to_save = []
            for x in range(0, len(body['options'])):
                option = body['options'][x]
                if 'deleted' in option:
                    if option['deleted'] == True:
                        to_delete.append(x)
                        continue
                if 'id' in option:
                    # se actualiza la opcion en la base de datos
                    option_update = OptionUpdate(**option)
                    mongodb.update_record('options', {'_id': ObjectId(option['id'])}, option_update)
                    to_save.append(option['id'])
                else:
                    # se crea la opcion en la base de datos
                    option = Option(**option)
                    resp = mongodb.insert_record('options', option)
                    # se agrega el id de la opcion al listado
                    to_save.append(str(resp.inserted_id))

            body['options'] = to_save

            list_update = ListUpdate(**body)

            resp = mongodb.update_record('lists', {'_id': ObjectId(id)}, list_update)
            # Registrar el log
            register_log(user, log_actions['list_update'], {'list': body})
            # Limpiar la cache
            update_cache()
            if(id == get_access_rights_id()):
                get_access_rights.invalidate_all()
            if(id == get_roles_id()):
                get_roles.invalidate_all()
                
            # Retornar el resultado
            return {'msg': _('List updated successfully')}, 200
    
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para eliminar un listado
def delete_by_id(id, user):
    try:
        # Buscar el listado en la base de datos
        lista = mongodb.get_record('lists', {'_id': ObjectId(id)})
        # Si el listado no existe, retornar error
        if not lista:
            return {'msg': 'Listado no existe'}, 404
        # Eliminar el listado de la base de datos
        mongodb.delete_record('lists', {'_id': ObjectId(id)})
        # Registrar el log
        register_log(user, log_actions['list_delete'], {'list': {
            'name': lista['name'],
            'id': lista['_id'],
        }})
        # Limpiar la cache
        get_by_id.invalidate_all()
        get_all.invalidate_all()
        # Retornar el resultado
        return {'msg': _('List deleted successfully')}, 200
    
    except Exception as e:
        return {'msg': str(e)}, 500