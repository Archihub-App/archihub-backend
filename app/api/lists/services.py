from flask import jsonify, request
from app.utils import DatabaseHandler
from bson import json_util
import json
from app.api.lists.models import List
from app.api.lists.models import ListUpdate
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para obtener todos los listados
def get_all():
    # Obtener todos los listados
    lists = mongodb.get_all_records('lists')
    # Quitar todos los campos menos el nombre y la descripción
    lists = [{ 'name': lista['name'], 'description': lista['description'], 'slug': lista['slug']} for lista in lists]
    # Retornar lists
    return jsonify(lists), 200

# Nuevo servicio para crear un estándar de metadatos
def create(body, user):
    # Crear instancia de List con el body del request
    try:
        lista = List(**body)
    except Exception as e:
        return {'msg': str(e)}, 400
    # Insertar el estándar de metadatos en la base de datos
    new_list = mongodb.insert_record('lists', lista)
    # Registrar el log
    register_log(user, log_actions['list_create'])
    # Retornar el resultado
    return {'msg': 'Listado creado exitosamente'}, 201

# Nuevo servicio para devolver un listado por su slug
def get_by_slug(slug):
    # Buscar el listado en la base de datos
    lista = mongodb.get_record('lists', {'slug': slug})
    # Si el listado no existe, retornar error
    if not lista:
        return {'msg': 'Listado no existe'}
    # quitamos el id del listado
    lista.pop('_id')
    # Parsear el resultado
    lista = parse_result(lista)
    # Retornar el resultado
    return lista

# Nuevo servicio para actualizar un listado
def update_by_slug(slug, body, user):
    # Buscar el listado en la base de datos
    lista = mongodb.get_record('lists', {'slug': slug})
    # Si el listado no existe, retornar error
    if not lista:
        return {'msg': 'Listado no existe'}, 404
    # Crear instancia de ListUpdate con el body del request
    try:
        list_update = ListUpdate(**body)
    except Exception as e:
        return {'msg': str(e)}, 400
    # Actualizar el listado en la base de datos
    mongodb.update_record('lists', {'slug': slug}, list_update)
    # Registrar el log
    register_log(user, log_actions['list_update'])
    # Retornar el resultado
    return {'msg': 'Listado actualizado exitosamente'}, 200