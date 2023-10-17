from app.utils import DatabaseHandler
from functools import lru_cache
from bson import json_util
import json
from bson.objectid import ObjectId

mongodb = DatabaseHandler.DatabaseHandler()


@lru_cache(maxsize=1)
def get_roles_id():
    try:
        # Obtener el registro access_rights de la colección system
        access_rights = mongodb.get_record('system', {'name': 'access_rights'})
        # Si el registro no existe, retornar error
        if not access_rights:
            raise Exception('No existe el registro access_rights')

        roles = access_rights['data'][1]['value']

        return roles
    except Exception as e:
        raise Exception(
            'Error al obtener el registro access_rights: ' + str(e))


@lru_cache(maxsize=1)
def get_roles():
    try:
        # Obtener el listado con roles
        list = get_list_by_id(get_roles_id())

        temp = [*list['options']]
        # Agregar admin y editor a la lista
        temp.append({'id': 'admin', 'term': 'admin'})
        temp.append({'id': 'editor', 'term': 'editor'})
        temp.append({'id': 'user', 'term': 'user'})
        temp.append({'id': 'processing', 'term': 'processing'})

        return {
            'options': temp
        }

    except Exception as e:
        raise Exception(
            'Error al obtener el registro access_rights: ' + str(e))

@lru_cache(maxsize=1)
def get_access_rights_id():
    try:
        # Obtener el registro access_rights de la colección system
        access_rights = mongodb.get_record('system', {'name': 'access_rights'})
        # Si el registro no existe, retornar error
        if not access_rights:
            raise Exception('No existe el registro access_rights')

        list_id = access_rights['data'][0]['value']

        return list_id

    except Exception as e:
        raise Exception('Error al obtener el registro access_rights')

@lru_cache(maxsize=1)
def get_access_rights():
    try:
        # Obtener el listado con list_id
        list = get_list_by_id(get_access_rights_id())

        return list

    except Exception as e:
        raise Exception('Error al obtener el registro access_rights')


def verify_role_exists(compare):
    roles = get_roles()['options']

    for role in compare:
        if role['id'] not in [r['id'] for r in roles]:
            raise Exception('El rol ' + role['id'] + ' no existe')

    return [role['id'] for role in roles]


def verify_accessright_exists(compare):
    access_rights = get_access_rights()['options']

    for access_right in compare:
        if access_right['id'] not in [r['id'] for r in access_rights]:
            raise Exception('El derecho de acceso ' +
                            access_right['id'] + ' no existe')

    return [role['id'] for role in access_rights]

def get_list_by_id(id):
    try:
        # Buscar el listado en la base de datos
        lista = mongodb.get_record('lists', {'_id': ObjectId(id)})
        # a lista solo le dejamos los campos name, description, y options
        lista = { 'name': lista['name'], 'description': lista['description'], 'options': lista['options'] }
        # Si el listado no existe, retornar error
        if not lista:
            return {'msg': 'Listado no existe'}
        
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
    
def parse_result(result):
    return json.loads(json_util.dumps(result))