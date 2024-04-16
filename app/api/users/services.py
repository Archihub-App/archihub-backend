from flask import jsonify
from app.utils import DatabaseHandler
from app.utils import CacheHandler
import bcrypt
from bson import json_util
import json
from app.api.users.models import User, UserUpdate
from datetime import timedelta
from flask_jwt_extended import create_access_token
from cryptography.fernet import Fernet
from bson.objectid import ObjectId
import re
from config import config
import os
import datetime
from app.utils.functions import get_access_rights, get_roles, verify_accessright_exists, verify_role_exists

fernet_key = config[os.environ['FLASK_ENV']].FERNET_KEY
mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()
fernet = Fernet(fernet_key)

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

def update_cache():
    has_right.invalidate_all()
    has_role.invalidate_all()
    get_by_username.invalidate_all()
    get_total.invalidate_all()

# Nueva funcion para devolver el usuario por su id
# @cacheHandler.cache.cache()
def get_by_id(id):
    try:
        # Obtener el usuario de la coleccion users
        user = mongodb.get_record('users', {'_id': ObjectId(id)}, fields={'password': 0, 'status': 0, 'photo': 0, 'compromise': 0, 'token': 0, 'adminToken': 0})

        # Si el usuario no existe, retornar error
        if not user:
            return {'msg': 'Recurso no existe'}, 404
        
        user['_id'] = str(user['_id'])

        # Retornar el resultado
        return user, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para obtener un usuario por su username
@cacheHandler.cache.cache()
def get_by_username(username):
    try:
        # Obtener el usuario de la coleccion users
        user = mongodb.get_record('users', {'username': username}, fields={'token': 1, 'adminToken': 1, 'requests': 1, 'lastRequest': 1})

        # Si el usuario no existe, retornar error
        if not user:
            return {'msg': 'Recurso no existe'}
        
        user['_id'] = str(user['_id'])
        # Retornar el resultado
        return user
    except Exception as e:
        raise Exception(str(e))
    
# Nuevo servicio para agregar una request y la fecha de la request
def add_request(username):
    try:
        # Obtener el usuario de la coleccion users
        user = mongodb.get_record('users', {'username': username}, fields={'requests': 1, 'lastRequest': 1})

        # Si el usuario no existe, retornar error
        if not user:
            return {'msg': 'Usuario no existe'}
        
        # Verificar que lastRequest sea una fecha y que sea de la semana actual
        if not 'lastRequest' in user:
            # Si no existe lastRequest, establecer el valor de requests a 1
            user['requests'] = 1
            user['lastRequest'] = datetime.datetime.now()
        elif not isinstance(user['lastRequest'], datetime.datetime) or not is_date_in_current_week(user['lastRequest']):
            # Si no es una fecha o no es de la semana actual, establecer el valor de requests a 1
            user['requests'] = 1
            user['lastRequest'] = datetime.datetime.now()
        elif user['requests'] < 2000 and is_date_in_current_week(user['lastRequest']):
            user['requests'] += 1
            user['lastRequest'] = datetime.datetime.now()
        else:
            raise Exception('Límite de requests excedido')
        
        user_update = UserUpdate(requests=user['requests'], lastRequest=user['lastRequest'])
        # Actualizar el usuario
        mongodb.update_record('users', {'username': username}, update_model=user_update)

    except Exception as e:
        raise Exception(str(e))

# Nuevo servicio para obtener todos los usuarios con filtros
def get_all(body, current_user):
    try:
        # Obtener todos los usuarios de la coleccion users
        users = list(mongodb.get_all_records(
            'users', body['filters'], limit=20, skip=body['page'] * 20, fields={'password': 0, 'status': 0, 'photo': 0, 'compromise': 0, 'token': 0, 'adminToken': 0}))
        
        total = get_total(json.dumps(body['filters']))

        rights = get_access_rights()
        if rights:
            rights = rights['options']
        
        roles = get_roles()
        if roles:
            roles = roles['options']

        for r in users:
            r['id'] = str(r['_id'])
            r.pop('_id')
            r['total'] = total

            rights_temp = []
            for right in r['accessRights']:
                _ = next((item for item in rights if item["id"] == right), None)
                if _:
                    rights_temp.append(_['term'])

            roles_temp = []
            for role in r['roles']:
                _ = next((item for item in roles if item["id"] == role), None)
                if _:
                    roles_temp.append(_['term'])
            
            r['accessRights'] = rights_temp
            r['roles'] = roles_temp

        # Retornar el resultado
        return parse_result(users), 200
    
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Funcion para obtener el total de recursos
@cacheHandler.cache.cache()
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

# Nuevo servicio para actualizar un usuario
def update_user(body, current_user):
    try:
        print(body)
        # Buscar el usuario en la base de datos
        user = mongodb.get_record('users', {'_id': ObjectId(body['_id'])}, fields={'lastRequest': 0})
        # Si el usuario no existe, retornar error
        if not user:
            return {'msg': 'Usuario no existe'}, 404
        
        if user['username'] != body['username']:
            return {'msg': 'Error con la equivalencia del usuario'}, 400
            
        body['roles'] = verify_role_exists(body['roles'])
        body['accessRights'] = verify_accessright_exists(body['accessRights'])

        body.pop('lastRequest')

        # Crear instancia de UserUpdate con el body del request
        user_update = UserUpdate(**body)
        # Actualizar usuario en la base de datos
        mongodb.update_record('users', {'_id': ObjectId(body['_id'])}, user_update)

        update_cache()
        # Retornar mensaje de éxito
        return {'msg': 'Usuario actualizado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para registrar un usuario
def register_user(body, user):
    try:
        # Verificar si el usuario ya existe
        user = mongodb.get_record('users', {'username': body['username']})
        # Si el usuario ya existe, retornar error
        if user:
            return jsonify({'msg': 'Usuario ya existe'}), 400
        
        password = body['password']
       
        roles = get_roles()['options']
        rights = get_access_rights()['options']

        for role in body['roles']:
            if role['id'] not in [r['id'] for r in roles]:
                return {'msg': 'Rol no existe'}, 400
            
        for right in body['accessRights']:
            if right['id'] not in [r['id'] for r in rights]:
                return {'msg': 'Permiso no existe'}, 400
            
        body['roles'] = [role['id'] for role in body['roles']]
        body['accessRights'] = [right['id'] for right in body['accessRights']]

        errors = {}
        validate_user_fields(body, errors)

        if errors:
            return {'msg': 'Error al validar los campos', 'errors': errors}, 400

        
        # Encriptar contraseña
        password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        body['password'] = password
        # Crear instancia de User con el body del request
        user = User(**body)

        # Insertar usuario en la base de datos
        mongodb.insert_record('users', user)
    
    
        return jsonify({'msg': 'Usuario registrado exitosamente'}), 201

    except Exception as e:
        return jsonify({'msg': str(e)}), 500
    
# Funcion para validar los campos de un usuario
def validate_user_fields(body, errors):
    try:
        value = body['name']
        label = 'Nombre'
        if not isinstance(value, str):
            raise Exception(f'El campo {label} debe ser de tipo string')
        # Si field.required entonces el valor no puede ser vacío o == ''
        if value == '' or value == None:
            raise Exception(f'El campo {label} es requerido')
    except Exception as e:
        errors['name'] = str(e)

    try:
        value = body['username']
        label = 'Nombre'
        if not isinstance(value, str):
            raise Exception(f'El campo {label} debe ser de tipo string')
        # Si field.required entonces el valor no puede ser vacío o == ''
        if value == '' or value == None:
            raise Exception(f'El campo {label} es requerido')
    except Exception as e:
        errors['username'] = str(e)

    try:
        value = body['username']
        label = 'Email'
        if not isinstance(value, str):
            raise Exception(f'El campo {label} debe ser de tipo string')
        # Si field.required entonces el valor no puede ser vacío o == ''
        if value == '' or value == None:
            raise Exception(f'El campo {label} es requerido')
        if not re.match(r"[^@]+@[^@]+\.[^@]+", value):
            raise Exception(f'El campo {label} debe ser un email')
    except Exception as e:
        errors['username'] = str(e)

    # validar si body['password'] es igual a body['confirmPassword']
    if body['password'] != body['confirmPassword']:
        errors['confirmPassword'] = 'Las contraseñas no coinciden'
    
    return errors

# Nuevo servicio para buscar un usuario por su username
def get_user(username):
    user = mongodb.get_record('users', {'username': username}, fields={'status': 0, 'photo': 0, 'requests': 0, 'lastRequest': 0})
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
@cacheHandler.cache.cache()
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

@cacheHandler.cache.cache()
def has_right(username, right):
    user = mongodb.get_record('users', {'username': username})
    # Si el usuario no existe, retornar error
    if not user:
        return jsonify({'msg': 'Usuario no existe'}), 400
    # Si el usuario tiene el rol, retornar True
    if right in user['accessRights']:
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
        # usamos Fernet para encriptar el token de acceso
        cipher = fernet.encrypt(access_token.encode('utf-8'))

        update = UserUpdate(token=cipher)
    else:
        access_token = create_access_token(identity=username, expires_delta=timedelta(days=2))
        # usamos Fernet para encriptar el token de acceso
        cipher = fernet.encrypt(access_token.encode('utf-8'))

        update = UserUpdate(adminToken=cipher)


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
    
# Funcion para devolver las requests de un usuario, si lastRequest no es de la semana actual, se establece requests a 0
def get_requests(username):
    try:
        # Obtener el usuario de la coleccion users
        user = mongodb.get_record('users', {'username': username}, fields={'requests': 1, 'lastRequest': 1})

        # Si el usuario no existe, retornar error
        if not user:
            return {'msg': 'Usuario no existe'}
        
        # Verificar que lastRequest sea una fecha y que sea de la semana actual
        if not 'lastRequest' in user:
            # Si no existe lastRequest, establecer el valor de requests a 1
            user['requests'] = 0
            user['lastRequest'] = datetime.datetime.now()
            user_update = UserUpdate(requests=user['requests'], lastRequest=user['lastRequest'])
            # Actualizar el usuario
            mongodb.update_record('users', {'username': username}, update_model=user_update)
        elif not isinstance(user['lastRequest'], datetime.datetime) or not is_date_in_current_week(user['lastRequest']):
            # Si no es una fecha o no es de la semana actual, establecer el valor de requests a 1
            user['requests'] = 0
            user['lastRequest'] = datetime.datetime.now()
            user_update = UserUpdate(requests=user['requests'], lastRequest=user['lastRequest'])
            # Actualizar el usuario
            mongodb.update_record('users', {'username': username}, update_model=user_update)

        # Retornar el resultado
        return parse_result(user)
    except Exception as e:
        raise Exception(str(e))
    
# Funcion que verifica que una fecha este dentro de la semana actual
def is_date_in_current_week(date):
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday <= date.date() <= sunday