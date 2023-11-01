from flask import request, jsonify
from functools import wraps
from config import config
from cryptography.fernet import Fernet
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
            return jsonify({'msg': 'No se ha enviado el token de autenticación'}), 401
        
        try:
            # se quita la palabra Bearer del token
            auth_header = auth_header.split(" ")[1]

            # se desencripta el token
            token = fernet.decrypt(auth_header.encode()).decode()

            print(token)

            decoded_token = jwt.decode(token, jwt_secret_key, algorithms=['HS256'])

            username = decoded_token['sub']
            isAdmin = False

            print(decoded_token)

            # verificar si el token tiene fecha de expiración
            if 'exp' in decoded_token:
                expiracion = decoded_token['exp']
                isAdmin = True
                
                if expiracion < time.time():
                    return jsonify({'msg': 'El token ha expirado'}), 401

            
            # obtener el usuario actual
            current_user = get_by_username(username)

            print(current_user)

            # verificar si el usuario existe
            if 'msg' in current_user:
                return jsonify({'msg': 'El usuario no existe'}), 401

            if not isAdmin:
                # verificar que el auth_header sea igual al token del usuario
                if auth_header != current_user['token']:
                    return jsonify({'msg': 'El token no es válido'}), 401
                
                try:
                    add_request(username)
                except Exception as e:
                    return jsonify({'msg': str(e)}), 401
                
            else:
                # verificar que el auth_header sea igual al token del usuario
                if auth_header != current_user['adminToken']:
                    return jsonify({'msg': 'El token no es válido'}), 401
                # verificar que el usuario tenga el rol de administrador
                if not has_role(username, 'admin'):
                    return jsonify({'msg': 'No tiene permisos para realizar esta acción'}), 401
            
            
        except Exception as e:
            return jsonify({'msg': str(e)}), 401
        
        return func(username, *arg, **kwargs)
        
    return wrapper
        
