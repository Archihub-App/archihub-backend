from app.api.auth import bp
from flask import jsonify, request
from flask_jwt_extended import create_access_token
from app.api.users.services import get_user
import bcrypt

@bp.route('/login', methods=['POST'])
def login():
    """
    Login para obtener el token de acceso
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
    
    # Obtener username y password del request
    username = request.json.get('username')
    password = request.json.get('password')
    
    # Buscar usuario en la base de datos
    user = get_user(username)

    # Verificar que el usuario exista
    if not user:
        return jsonify({'msg': 'Usuario inválido'}), 401
    # Verificar que la contraseña sea correcta
    if not bcrypt.checkpw(password.encode('utf-8'), user['password']):
        return jsonify({'msg': 'Contraseña inválida'}), 401
    
    # Crear el token de acceso
    access_token = create_access_token(username=username)
    # Retornar el token de acceso
    return jsonify({'access_token': access_token}), 200