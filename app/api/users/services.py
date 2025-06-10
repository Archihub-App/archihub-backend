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
from flask_babel import _

fernet_key = config[os.environ['FLASK_ENV']].FERNET_KEY
mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()
fernet = Fernet(fernet_key)

REDIRECT_URL = os.environ['REDIRECT_URL']

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

def update_cache():
    has_right.invalidate_all()
    has_role.invalidate_all()
    get_total.invalidate_all()

# Nueva funcion para devolver el usuario por su id
def get_by_id(id):
    try:
        # Obtener el usuario de la coleccion users
        user = mongodb.get_record('users', {'_id': ObjectId(id)}, fields={'password': 0, 'status': 0, 'photo': 0, 'compromise': 0, 'token': 0, 'adminToken': 0, 'nodeToken': 0})

        # Si el usuario no existe, retornar error
        if not user:
            return {'msg': _('User not found')}, 404
        
        user['_id'] = str(user['_id'])
        
        if 'favorites' not in user:
            user['favorites'] = []

        # Retornar el resultado
        return user, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para obtener un usuario por su username
def get_by_username(username):
    try:
        # Obtener el usuario de la coleccion users
        user = mongodb.get_record('users', {'username': username}, fields={'token': 1, 'adminToken': 1, 'requests': 1, 'lastRequest': 1, 'nodeToken': 1})

        # Si el usuario no existe, retornar error
        if not user:
            return {'msg': _('User not found')}, 404
        
        user['_id'] = str(user['_id'])
        if 'favorites' not in user:
            user['favorites'] = []
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
            return {'msg': _('User not found')}, 404
        
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
            raise Exception(_('You have reached the limit of requests for this week'))

        user_update = UserUpdate(requests=user['requests'], lastRequest=user['lastRequest'])
        # Actualizar el usuario
        mongodb.update_record('users', {'username': username}, update_model=user_update)

    except Exception as e:
        raise Exception(str(e))

# Nuevo servicio para obtener todos los usuarios con filtros
def get_all(body, current_user):
    try:
        page = body['page'] if 'page' in body else 0
        filters = body['filters'] if 'filters' in body else {}
        users = list(mongodb.get_all_records(
            'users', filters, limit=20, skip=page * 20, fields={'password': 0, 'status': 0, 'photo': 0, 'compromise': 0, 'token': 0, 'adminToken': 0, 'nodeToken': 0, 'vizToken': 0, 'requests': 0, 'lastRequest': 0, 'favorites': 0}, sort=[('name', 1)]))
        
        total = get_total(json.dumps(filters))

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
        # Buscar el usuario en la base de datos
        user = mongodb.get_record('users', {'_id': ObjectId(body['_id'])}, fields={'lastRequest': 0})
        # Si el usuario no existe, retornar error
        if not user:
            return {'msg': _('User not found')}, 404
        
        if user['username'] != body['username']:
            return {'msg': _('You cannot change the username')}, 400
            
        body['roles'] = verify_role_exists(body['roles'])
        if 'editor' not in body['roles'] and 'admin' not in body['roles'] and 'user' not in body['roles']:
            return {'msg': _('You must have at least one system role')}, 400
        
        body['accessRights'] = verify_accessright_exists(body['accessRights'])

        if 'lastRequest' in body:
            body.pop('lastRequest')

        # Crear instancia de UserUpdate con el body del request
        user_update = UserUpdate(**body)
        # Actualizar usuario en la base de datos
        mongodb.update_record('users', {'_id': ObjectId(body['_id'])}, user_update)

        update_cache()
        # Retornar mensaje de éxito
        return {'msg': _('User updated successfully')}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
def update_me(body, current_user):
    try:
        # Buscar el usuario en la base de datos
        user = mongodb.get_record('users', {'username': current_user}, fields={'password': 1, 'name': 1})
        # Si el usuario no existe, retornar error
        if not user:
            return jsonify({'msg': _('User not found')}), 404
        
        password = body['password']
        # Si la contraseña no coincide, retornar error
        if not bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
            return jsonify({'msg': _('Incorrect password')}), 400
        
        update = False
        update_payload = {}

        if body['name'] != user['name']:
            update_payload['name'] = body['name']
            update = True

        if body['new_password'] != '' and body['new_password_confirmation'] != '':
            if body['new_password'] != body['new_password_confirmation']:
                return jsonify({'msg': _('Passwords do not match')}), 400
            update_payload['password'] = bcrypt.hashpw(body['new_password'].encode('utf-8'), bcrypt.gensalt())
            update = True

        if update:
            user_update = UserUpdate(**update_payload)
            mongodb.update_record('users', {'username': current_user}, user_update)
            return jsonify({'msg': _('User updated successfully')}), 200
        else:
            return jsonify({'msg': _('No changes were made')}), 400
        
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para registrar un usuario
def register_user(body):
    try:
        # Verificar si el usuario ya existe
        user = mongodb.get_record('users', {'username': body['username']})
        # Si el usuario ya existe, retornar error
        if user:
            return jsonify({'msg': _('User already exists')}), 400
        
        password = body['password']
       
        roles = get_roles()['options']
        rights = get_access_rights()['options']

        for role in body['roles']:
            if role['id'] not in [r['id'] for r in roles]:
                return {'msg': _('Role does not exist')}, 400
            
        for right in body['accessRights']:
            if right['id'] not in [r['id'] for r in rights]:
                return {'msg': _('Access right does not exist')}, 400
            
        body['roles'] = [role['id'] for role in body['roles']]
        body['accessRights'] = [right['id'] for right in body['accessRights']]

        errors = {}
        validate_user_fields(body, errors)

        if errors:
            return {'msg': _('Error validating fields'), 'errors': errors}, 400

        
        # Encriptar contraseña
        if password != '':
            password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        body['password'] = password
        # Crear instancia de User con el body del request
        user = User(**body)

        # Insertar usuario en la base de datos
        mongodb.insert_record('users', user)
    
    
        return jsonify({'msg': _('User registered successfully')}), 201

    except Exception as e:
        return jsonify({'msg': str(e)}), 500
    
def forgot_password(body):
    try:
        admin_register_user = mongodb.get_record('system', {'name': 'user_management'})
        admin_register_user = admin_register_user['data'][1]
        if 'value' not in admin_register_user or not admin_register_user['value']:
            return jsonify({'msg': _('Password recovery disabled')}), 400
    
        user = mongodb.get_record('users', {'username': body['username']})
        if not user:
            return {'msg': _('User does not exist')}, 404
        
        from app.api.email.services import send_email
        from app.api.email.templates import forgot_password_template

        token = create_access_token(identity=body['username'], expires_delta=timedelta(days=1))
        token = fernet.encrypt(token.encode('utf-8'))

        link = f"{REDIRECT_URL}/reset-password?token={token.decode('utf-8')}"

        send_email(body['username'], _('Password recovery'), forgot_password_template(link))

        return {'msg': _('Password recovery email sent')}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
def delete_user(body, user):
    try:
        if body['username'] == user:
            return {'msg': _('You cannot delete yourself')}, 400
        
        user = mongodb.get_record('users', {'username': body['username']})
        if not user:
            return {'msg': _('User does not exist')}, 404
        
        mongodb.delete_record('users', {'username': body['username']})
        
        return {'msg': _('User deleted successfully')}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
def register_me(body):
    try:
        admin_register_user = mongodb.get_record('system', {'name': 'user_management'})
        admin_register_user = admin_register_user['data'][0]
        if 'value' not in admin_register_user or not admin_register_user['value']:
            return jsonify({'msg': _('User registration disabled')}), 400
        
        # Verificar si el usuario ya existe
        user = mongodb.get_record('users', {'username': body['username']})
        # Si el usuario ya existe, retornar error
        if user:
            return jsonify({'msg': _('User already exists')}), 400
        
        password = body['password']
            
        body['roles'] = ['user']
        body['accessRights'] = []

        errors = {}
        validate_user_fields(body, errors)

        if errors:
            return {'msg': _('Error validating fields'), 'errors': errors}, 400
        
        # Encriptar contraseña
        if password != '':
            password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
            
        body['password'] = password
        body['verified'] = False
        # Crear instancia de User con el body del request
        user = User(**body)

        from app.api.email.services import send_email
        from app.api.email.templates import new_user_verification_template
        
        token = create_access_token(identity=body['username'], expires_delta=timedelta(days=1))
        token = fernet.encrypt(token.encode('utf-8'))
        
        link = f"{REDIRECT_URL}/verify-account?token={token.decode('utf-8')}"
        
        send_email(body['username'], _('Account verification'), new_user_verification_template(link))
        
        mongodb.insert_record('users', user)
    
        return jsonify({'msg': _('User registered successfully. Please verify your account by clicking the link sent to your email')}), 201

    except Exception as e:
        return jsonify({'msg': str(e)}), 500
    
# Funcion para validar los campos de un usuario
def validate_user_fields(body, errors):
    try:
        value = body['name']
        label = 'Nombre'
        if not isinstance(value, str):
            raise Exception(_(u'The field {label} must be of type string', label=label))
        # Si field.required entonces el valor no puede ser vacío o == ''
        if value == '' or value == None:
            raise Exception(_(u'The field {label} is required', label=label))
    except Exception as e:
        errors['name'] = str(e)

    try:
        value = body['username']
        label = 'Nombre'
        if not isinstance(value, str):
            raise Exception(_(u'The field {label} must be of type string', label=label))
        # Si field.required entonces el valor no puede ser vacío o == ''
        if value == '' or value == None:
            raise Exception(_(u'The field {label} is required', label=label))
    except Exception as e:
        errors['username'] = str(e)

    try:
        value = body['username']
        label = 'Email'
        if not isinstance(value, str):
            raise Exception(_(u'The field {label} must be of type string', label=label))
        # Si field.required entonces el valor no puede ser vacío o == ''
        if value == '' or value == None:
            raise Exception(_(u'The field {label} is required', label=label))
        if not re.match(r"[^@]+@[^@]+\.[^@]+", value):
            raise Exception(_(u'The field {label} must be a valid email', label=label))
    except Exception as e:
        errors['username'] = str(e)

    # validar si body['password'] es igual a body['confirmPassword']
    if body['password'] != body['confirmPassword']:
        errors['confirmPassword'] = _('Passwords do not match')
    
    return errors

# Nuevo servicio para buscar un usuario por su username
def get_user(username):
    user = mongodb.get_record('users', {'username': username}, fields={'status': 0, 'photo': 0, 'requests': 0, 'lastRequest': 0})
    # retornar el resultado
    if not user:
        return None
    
    if 'verified' not in user:
        user['verified'] = True
    elif user['verified'] == False:
        return None
    
    if 'favorites' not in user:
        user['favorites'] = []
            
    return parse_result(user)

# Nuevo servicio para aceptar el compromiso de un usuario
def accept_compromise(username):
    # Nueva instancia de UserUpdate con el compromiso aceptado
    user_update = UserUpdate(compromise=True)
    # Actualizar usuario en la base de datos
    mongodb.update_record('users', {'username': username}, user_update)
    # Retornar mensaje de éxito
    return jsonify({'msg': _('Compromise accepted successfully')}), 200

# Nuevo servicio para verificar si el usuario tiene un rol específico
@cacheHandler.cache.cache()
def has_role(username, role):
    user = mongodb.get_record('users', {'username': username})
    # Si el usuario no existe, retornar error
    if not user:
        return jsonify({'msg': _('User does not exist')}), 400
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
        return jsonify({'msg': _('User does not exist')}), 400
    # Si el usuario tiene el rol, retornar True
    if right in user['accessRights']:
        return True
    # Si el usuario no tiene el rol, retornar False
    return False

def get_user_rights(username):
    user = mongodb.get_record('users', {'username': username}, fields={'accessRights': 1})
    # Si el usuario no existe, retornar error
    if not user:
        return jsonify({'msg': _('User does not exist')}), 400
    # Retornar los roles del usuario
    return user['accessRights']

# Nuevo servicio para generar un token de autenticación para usar la API publica
def generate_token(username, password, admin = False, expiration = 2):
    if expiration:
        expiration = timedelta(days=expiration)
    else:
        expiration = False
    # Buscar el usuario en la base de datos
    user = mongodb.get_record('users', {'username': username})
    # Si el usuario no existe, retornar error
    if not user:
        return jsonify({'msg': _('User does not exist')}), 400
    
    # Si la contraseña no coincide, retornar error
    if not bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
        return jsonify({'msg': _('Incorrect password')}), 400
    # Si el usuario no ha aceptado el compromiso, retornar error
    # if not user['compromise']:
    #     return jsonify({'msg': _('User has not accepted the compromise')}), 400
    
    # Crear el token de acceso para el usuario con el username y sin expiración
    if not admin:
        access_token = create_access_token(identity=username, expires_delta=False)
        # usamos Fernet para encriptar el token de acceso
        cipher = fernet.encrypt(access_token.encode('utf-8'))

        update = UserUpdate(token=cipher)
    else:
        access_token = create_access_token(identity=username, expires_delta=expiration)
        # usamos Fernet para encriptar el token de acceso
        cipher = fernet.encrypt(access_token.encode('utf-8'))

        update = UserUpdate(adminToken=cipher)


    # guardar el token de acceso en la base de datos
    mongodb.update_record('users', {'username': username}, update)

    # Retornar el token de acceso
    return jsonify({'access_token': access_token}), 200

def generate_node_token(username, password):
    # Buscar el usuario en la base de datos
    user = mongodb.get_record('users', {'username': username})
    # Si el usuario no existe, retornar error
    if not user:
        return jsonify({'msg': _('User does not exist')}), 400
    
    # Si la contraseña no coincide, retornar error
    if not bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
        return jsonify({'msg': _('Incorrect password')}), 400
    
    # Si el usuario no ha aceptado el compromiso, retornar error
    # if not user['compromise']:
    #     return jsonify({'msg': _('User has not accepted the compromise')}), 400
    
    access_token = create_access_token(identity=username, expires_delta=False)
    # usamos Fernet para encriptar el token de acceso
    cipher = fernet.encrypt(access_token.encode('utf-8'))

    # encrypt the access token
    update = UserUpdate(nodeToken=cipher)

    # guardar el token de acceso en la base de datos
    mongodb.update_record('users', {'username': username}, update)

    # Retornar el token de acceso
    return jsonify({'access_token': access_token}), 200

def generate_viz_token(username, password):
    # Buscar el usuario en la base de datos
    user = mongodb.get_record('users', {'username': username})
    # Si el usuario no existe, retornar error
    if not user:
        return jsonify({'msg': _('User does not exist')}), 400
    
    # Si la contraseña no coincide, retornar error
    if not bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
        return jsonify({'msg': _('Incorrect password')}), 400
    
    # Si el usuario no ha aceptado el compromiso, retornar error
    # if not user['compromise']:
    #     return jsonify({'msg': _('User has not accepted the compromise')}), 400
    
    access_token = create_access_token(identity=username, expires_delta=False)
    # usamos Fernet para encriptar el token de acceso
    cipher = fernet.encrypt(access_token.encode('utf-8'))

    # encrypt the access token
    update = UserUpdate(vizToken=cipher)

    # guardar el token de acceso en la base de datos
    mongodb.update_record('users', {'username': username}, update)

    # Retornar el token de acceso
    return jsonify({'access_token': access_token}), 200
    
# Funcion para devolver las requests de un usuario, si lastRequest no es de la semana actual, se establece requests a 0
def get_requests(username):
    try:
        # Obtener el usuario de la coleccion users
        user = mongodb.get_record('users', {'username': username}, fields={'requests': 1, 'lastRequest': 1})

        # Si el usuario no existe, retornar error
        if not user:
            return {'msg': _('User not found')}, 404
        
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

def set_favorite(user, body):
    try:
        update = {
            '$addToSet': {
                'favorites': {
                    'type': body['type'],
                    'id': body['id'],
                    'view': body['view'] if 'view' in body else None
                }
            }
        }

        fav = mongodb.get_record(body['type'], {'_id': ObjectId(body['id'])}, fields={'_id': 1, 'status': 1})

        if not fav:
            return {'msg': _('Resource not found')}, 404
        elif fav['status'] != 'published' and body['type'] == 'resources':
            return {'msg': _('Resource not published')}, 400

        resp = mongodb.update_record_operator('users', {'username': user}, update)

        if body['type'] == 'resource':
            from app.api.resources.services import add_to_favCount
            add_to_favCount(body['id'])
        elif body['type'] == 'record':
            from app.api.records.services import add_to_favCount
            add_to_favCount(body['id'])

        get_user_favorites.invalidate(user)

        return {'msg': _('Favorite added successfully')}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
def delete_favorite(user, body):
    try:
        update = {
            '$pull': {
                'favorites': {
                    'type': body['type'],
                    'id': body['id']
                }
            }
        }

        resp = mongodb.update_record_operator('users', {'username': user}, update)

        if body['type'] == 'resource':
            from app.api.resources.services import remove_from_favCount
            remove_from_favCount(body['id'])
        elif body['type'] == 'record':
            from app.api.records.services import remove_from_favCount
            remove_from_favCount(body['id'])

        get_user_favorites.invalidate(user)

        return {'msg': _('Favorite deleted successfully')}, 200
    except Exception as e:
        return {'msg': str(e)}, 500

@cacheHandler.cache.cache()
def get_user_favorites(username):
    try:
        user_fav = mongodb.get_record('users', {'username': username}, fields={'favorites': 1})
        user_fav = user_fav['favorites']
        return user_fav
    except Exception as e:
        return {'msg': str(e)}, 500

def get_favorites(user, body):
    try:
        user_fav = get_user_favorites(user)
        user_fav = [fav for fav in user_fav if fav['type'] == body['type']]
        total = len(user_fav)

        filters = {
            '_id': {
                '$in': [ObjectId(fav['id']) for fav in user_fav]
            },
        }

        if body['type'] == 'resources':
            filters['status'] = 'published'
            fields = {'metadata.firstLevel.title': 1}
            sort = [('metadata.firstLevel.title', 1)]
        else:
            fields = {'name': 1, 'displayName': 1}
            sort = [('name', 1)]

        resp = list(mongodb.get_all_records(body['type'], filters, limit=20, skip=body['page'] * 20, fields=fields, sort=sort))
        for r in resp:
            r['id'] = str(r['_id'])
            fav = next((fav for fav in user_fav if fav['id'] == r['id']), None)
            r['view'] = fav['view'] if 'view' in fav else None
            r.pop('_id')

        resp = {
            'total': total,
            'results': resp
        }

        return resp, 200
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500
    

def get_user_snaps(user, type):
    try:
        from app.api.snaps.services import get_by_user_id
        snaps = get_by_user_id(user, type)
        return snaps, 200
    except Exception as e:
        return {'msg': str(e)}, 500

# Funcion que verifica que una fecha este dentro de la semana actual
def is_date_in_current_week(date):
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday <= date.date() <= sunday