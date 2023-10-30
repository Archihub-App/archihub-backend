from app.api.auth import bp
from flask import jsonify, request
from flask_jwt_extended import create_access_token
from app.api.users.services import get_user
import bcrypt
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from datetime import timedelta

# En este archivo se registran las rutas de la API para la autenticación

# Nuevo endpoint para hacer login
@bp.route('/login', methods=['POST'])
def login():
    """
    Login para obtener el token de acceso al gestor documental
    ---
    tags:
        - Auth
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                username:
                    type: string
                password:
                    type: string
            required:
                - username
                - password
    responses:
        200:
            description: Login exitoso
        401:
            description: Usuario o contraseña inválidos
    """
    print(request.json)
    # Obtener username y password del request
    username = request.json.get('username')
    password = request.json.get('password')
    
    # Buscar usuario en la base de datos
    user = get_user(username)

    print(user)

    # Verificar que el usuario exista
    if not user:
        return jsonify({'msg': 'Usuario inválido'}), 401
    # Verificar que la contraseña sea correcta
    if not bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
        return jsonify({'msg': 'Contraseña inválida'}), 401
    
    # expiración del token de acceso
    expires_delta = timedelta(days=1)
    # Crear el token de acceso para el usuario con el username
    access_token = create_access_token(identity=username, expires_delta=expires_delta)

    # Registrar el log de login
    register_log(username, log_actions['user_login'])

    # Retornar el token de acceso
    return jsonify({'access_token': access_token}), 200