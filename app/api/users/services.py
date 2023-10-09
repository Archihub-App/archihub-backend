from flask import jsonify
from app.utils import DatabaseHandler
import bcrypt
from bson import json_util
import json
from app.api.users.models import User, UserUpdate
from datetime import timedelta
from flask_jwt_extended import create_access_token
from cryptography.fernet import Fernet
from functools import lru_cache
from bson.objectid import ObjectId

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nueva funcion para devolver el usuario por su id
def get_by_id(id):
    try:
        # Obtener el usuario de la coleccion users
        user = mongodb.get_record('users', {'_id': ObjectId(id)}, fields={'password': 0, 'status': 0, 'photo': 0, 'compromise': 0, 'token': 0, 'adminToken': 0})

        # Si el usuario no existe, retornar error
        if not user:
            return {'msg': 'Recurso no existe'}, 404
        # Retornar el resultado
        return parse_result(user), 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para obtener todos los usuarios con filtros
def get_all(body, current_user):
    try:
        # Obtener todos los usuarios de la coleccion users
        users = list(mongodb.get_all_records(
            'users', body['filters'], limit=20, skip=body['page'] * 20, fields={'password': 0, 'status': 0, 'photo': 0, 'compromise': 0, 'token': 0, 'adminToken': 0}))
        
        if not users:
            return {'msg': 'Recurso no existe'}, 404
        
        total = get_total(json.dumps(body['filters']))


        for r in users:
            r['id'] = str(r['_id'])
            r.pop('_id')
            r['total'] = total
        # Retornar el resultado
        return parse_result(users), 200
    
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Funcion para obtener el total de recursos
@lru_cache(maxsize=500)
def get_total(obj):
    try:
        # convertir string a dict
        obj = json.loads(obj)
        # Obtener el total de recursos
        total = mongodb.count('users', obj)
        # Retornar el total
        return total
    except Exception as e:
        raise Exception(str(e))

# Nuevo servicio para registrar un usuario
def register_user(username, password):
    # Verificar si el usuario ya existe
    user = mongodb.get_record('users', {'username': username})
    # Si el usuario ya existe, retornar error
    if user:
        return jsonify({'msg': 'Usuario ya existe'}), 400
    # Encriptar contraseña
    password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    # Crear instancia de User con el username y la contraseña encriptada
    user_new = User(username=username, password=password)
    # Insertar usuario en la base de datos
    mongodb.insert_record('users', user_new)
    # Retornar mensaje de éxito
    return jsonify({'msg': 'Usuario registrado exitosamente'}), 200

# Nuevo servicio para actualizar un usuario
def update_user(username, password):
    # Encriptar contraseña
    password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    # Actualizar usuario en la base de datos
    mongodb.update_record('users', {'username': username}, {'password': password})
    # Retornar mensaje de éxito
    return jsonify({'msg': 'Usuario actualizado exitosamente'}), 200

# Nuevo servicio para buscar un usuario por su username
def get_user(username):
    user = mongodb.get_record('users', {'username': username})
    # retornar el resultado
    if not user:
        return None
    return parse_result(user)

# Nuevo servicio para aceptar el compromiso de un usuario
def accept_compromise(username):
    # Nueva instancia de UserUpdate con el compromiso aceptado
    user_update = UserUpdate(compromise=True)
    # Actualizar usuario en la base de datos
    mongodb.update_record('users', {'username': username}, user_update)
    # Retornar mensaje de éxito
    return jsonify({'msg': 'Compromiso aceptado exitosamente'}), 200

# Nuevo servicio para verificar si el usuario tiene un rol específico
def has_role(username, role):
    user = mongodb.get_record('users', {'username': username})
    # Si el usuario no existe, retornar error
    if not user:
        return jsonify({'msg': 'Usuario no existe'}), 400
    # Si el usuario tiene el rol, retornar True
    if role in user['roles']:
        return True
    # Si el usuario no tiene el rol, retornar False
    return False

# Nuevo servicio para generar un token de autenticación para usar la API publica
def generate_token(username, password, admin = False):
    # Buscar el usuario en la base de datos
    user = mongodb.get_record('users', {'username': username})
    # Si el usuario no existe, retornar error
    if not user:
        return jsonify({'msg': 'Usuario no existe'}), 400
    
    # Si la contraseña no coincide, retornar error
    if not bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
        return jsonify({'msg': 'Contraseña incorrecta'}), 400
    # Si el usuario no ha aceptado el compromiso, retornar error
    if not user['compromise']:
        return jsonify({'msg': 'Usuario no ha aceptado el compromiso'}), 400
    
    # Crear el token de acceso para el usuario con el username y sin expiración
    if not admin:
        access_token = create_access_token(identity=username, expires_delta=False)
        update = UserUpdate(token=access_token)
    else:
        access_token = create_access_token(identity=username, expires_delta=timedelta(days=2))
        update = UserUpdate(adminToken=access_token)


    # guardar el token de acceso en la base de datos
    mongodb.update_record('users', {'username': username}, update)

    # Retornar el token de acceso
    return jsonify({'access_token': access_token}), 200

# Nuevo servicio que devuelve el token de acceso de un usuario
def get_token(username):
    # Buscar el usuario en la base de datos
    user = mongodb.get_record('users', {'username': username})
    # Si el usuario no existe, retornar error
    if not user:
        return jsonify({'msg': 'Usuario no existe'}), 400
    # Si el usuario no ha aceptado el compromiso, retornar error
    if not user['compromise']:
        return jsonify({'msg': 'Usuario no ha aceptado el compromiso'}), 400
    # Si el usuario no tiene token de acceso, retornar error
    if not user['access_token']:
        return jsonify({'msg': 'Usuario no tiene token de acceso'}), 400
    # Retornar el token de acceso
    return jsonify({'access_token': user['access_token']}), 200