from app.api.users import bp
from flask import jsonify
from app.utils import DatabaseHandler
import bcrypt

mongodb = DatabaseHandler('sim-backend-prod')

# Nuevo servicio para registrar un usuario
def register_user(username, password):
    # Verificar si el usuario ya existe
    user = mongodb.get_record('users', {'username': username})
    # Si el usuario ya existe, retornar error
    if user:
        return jsonify({'msg': 'Usuario ya existe'}), 400
    # Encriptar contraseña
    password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    # Insertar usuario en la base de datos
    mongodb.insert_record('users', {'username': username, 'password': password})
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
    if not user:
        return None
    return user