from flask import jsonify
from app.utils import DatabaseHandler
import bcrypt
from bson import json_util
import json
from app.api.users.models import User, UserUpdate
from datetime import timedelta
from flask_jwt_extended import create_access_token

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

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
    user_update = UserUpdate(compromise_accepted=True)
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
def generate_token(username, password):
    # Buscar el usuario en la base de datos
    user = mongodb.get_record('users', {'username': username})
    # Si el usuario no existe, retornar error
    if not user:
        return jsonify({'msg': 'Usuario no existe'}), 400
    # Si la contraseña no coincide, retornar error
    if not bcrypt.checkpw(password.encode('utf-8'), user['password']):
        return jsonify({'msg': 'Contraseña incorrecta'}), 400
    # Si el usuario no ha aceptado el compromiso, retornar error
    if not user['compromise_accepted']:
        return jsonify({'msg': 'Usuario no ha aceptado el compromiso'}), 400
    
    # Crear el token de acceso para el usuario con el username y sin expiración
    access_token = create_access_token(identity=username, expires_delta=False)

    update = UserUpdate(access_token=access_token)
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
    if not user['compromise_accepted']:
        return jsonify({'msg': 'Usuario no ha aceptado el compromiso'}), 400
    # Si el usuario no tiene token de acceso, retornar error
    if not user['access_token']:
        return jsonify({'msg': 'Usuario no tiene token de acceso'}), 400
    # Retornar el token de acceso
    return jsonify({'access_token': user['access_token']}), 200