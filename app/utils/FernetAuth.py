from flask import request, jsonify
from functools import wraps
from config import config
from cryptography.fernet import Fernet
from flask_babel import gettext as _
import os
import jwt
import time
import datetime
from app.api.users.services import get_by_username, has_role, add_request

fernet_key = config[os.environ['FLASK_ENV']].FERNET_KEY
jwt_secret_key = config[os.environ['FLASK_ENV']].JWT_SECRET_KEY
fernet = Fernet(fernet_key)

def fernetAuthenticate(func):
    @wraps(func)
    def wrapper(*arg, **kwargs):
        auth_header = request.headers.get('Authorization')

        if not auth_header:
            return jsonify({'msg': _('Authentication token was not provided')}), 401
        
        try:
            # se quita la palabra Bearer del token
            auth_header = auth_header.split(" ")[1]

            # se desencripta el token
            token = fernet.decrypt(auth_header.encode()).decode()

            decoded_token = jwt.decode(token, jwt_secret_key, algorithms=['HS256'])

            username = decoded_token['sub']
            isAdmin = False

            # verificar si el token tiene fecha de expiración
            if 'exp' in decoded_token:
                expiracion = decoded_token['exp']
                if expiracion < time.time():
                    return jsonify({'msg': _('The token has expired')}), 401

            
            # obtener el usuario actual
            current_user = get_by_username(username)
            if has_role(username, 'admin'):
                isAdmin = True

            # verificar si el usuario existe
            if 'msg' in current_user:
                return jsonify({'msg': _('The user does not exist')}), 401

            if not isAdmin:
                # verificar que el auth_header sea igual al token del usuario
                if auth_header != current_user['token']:
                    return jsonify({'msg': _('The token is not valid')}), 401
                
                try:
                    add_request(username)
                except Exception as e:
                    return jsonify({'msg': str(e)}), 401
                
            else:
                # verificar que el auth_header sea igual al token del usuario
                if auth_header != current_user['adminToken']:
                    return jsonify({'msg': _('The token is not valid')}), 401
                # verificar que el usuario tenga el rol de administrador
                if not has_role(username, 'admin'):
                    return jsonify({'msg': _('You do not have permission to perform this action')}), 401


        except Exception as e:
            # This wraps token decrypt/decode (Fernet, PyJWT) — the real
            # exception text (library internals, stack context) isn't safe
            # to hand to an unauthenticated-until-proven-otherwise caller
            # probing the auth boundary. Log it server-side, return a
            # generic message. (The add_request except block above is
            # intentionally untouched — that one surfaces a real,
            # translated, user-facing rate-limit message, not a technical
            # exception.)
            print(f"fernetAuthenticate error: {e}")
            return jsonify({'msg': _('Invalid or expired token')}), 401

        return func(username, isAdmin, *arg, **kwargs)

    return wrapper

def publicFernetAuthenticate(func):
    @wraps(func)
    def wrapper(*arg, **kwargs):
        auth_header = request.headers.get('Authorization')

        if not auth_header:
            return jsonify({'msg': _('Authentication token was not provided')}), 401
        
        try:
            # se quita la palabra Bearer del token
            auth_header = auth_header.split(" ")[1]

            # se desencripta el token
            token = fernet.decrypt(auth_header.encode()).decode()

            decoded_token = jwt.decode(token, jwt_secret_key, algorithms=['HS256'])

            username = decoded_token['sub']
            isAdmin = False

            # verificar si el token tiene fecha de expiración (jwt.decode ya
            # rechaza tokens vencidos por su cuenta cuando 'exp' está
            # presente, pero se deja explícito por consistencia con
            # fernetAuthenticate/nodeFernetAuthenticate y para dar un
            # mensaje traducido en vez del genérico de la librería)
            if 'exp' in decoded_token:
                expiracion = decoded_token['exp']
                if expiracion < time.time():
                    return jsonify({'msg': _('The token has expired')}), 401

            # obtener el usuario actual
            current_user = get_by_username(username)
            if has_role(username, 'admin'):
                isAdmin = True

            # verificar si el usuario existe
            if 'msg' in current_user:
                return jsonify({'msg': _('The user does not exist')}), 401

            if not isAdmin:
                # verificar que el auth_header sea igual al token del usuario
                if auth_header != current_user['token']:
                    return jsonify({'msg': _('The token is not valid')}), 401

                try:
                    add_request(username)
                except Exception as e:
                    return jsonify({'msg': str(e)}), 401

            else:
                # verificar que el auth_header sea igual al token del usuario
                if auth_header != current_user['token']:
                    return jsonify({'msg': _('The token is not valid')}), 401
                # verificar que el usuario tenga el rol de administrador
                if not has_role(username, 'admin'):
                    return jsonify({'msg': _('You do not have permission to perform this action')}), 401


        except Exception as e:
            # See fernetAuthenticate's matching comment above.
            print(f"publicFernetAuthenticate error: {e}")
            return jsonify({'msg': _('Invalid or expired token')}), 401

        return func(username, isAdmin, *arg, **kwargs)

    return wrapper


def nodeFernetAuthenticate(func):
    @wraps(func)
    def wrapper(*arg, **kwargs):
        auth_header = request.headers.get('Authorization')

        if not auth_header:
            return jsonify({'msg': _('Authentication token was not provided')}), 401
        
        try:

            # se quita la palabra Bearer del token
            auth_header = auth_header.split(" ")[1]

            # se desencripta el token
            token = fernet.decrypt(auth_header.encode()).decode()

            decoded_token = jwt.decode(token, jwt_secret_key, algorithms=['HS256'])

            username = decoded_token['sub']

            # verificar si el token tiene fecha de expiración
            if 'exp' in decoded_token:
                expiracion = decoded_token['exp']
                if expiracion < time.time():
                    return jsonify({'msg': _('The token has expired')}), 401

            # obtener el usuario actual
            current_user = get_by_username(username)

            if not has_role(username, 'admin'):
                return jsonify({'msg': _('You do not have permission to perform this action')}), 401
        
            # verificar que el auth_header sea igual al token del usuario
            if auth_header != current_user['nodeToken']:
                return jsonify({'msg': _('The token is not valid')}), 401

        except Exception as e:
            # See fernetAuthenticate's matching comment above.
            print(f"nodeFernetAuthenticate error: {e}")
            return jsonify({'msg': _('Invalid or expired token')}), 401
        
        return func(username, *arg, **kwargs)
        
    return wrapper
        
