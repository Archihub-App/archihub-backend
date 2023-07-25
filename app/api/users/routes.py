from app.api.users import bp
from flask import jsonify
from flask import request
from . import services

# En este archivo se registran las rutas de la API para los usuarios

# Nuevo endpoint para registrar un usuario
@bp.route('/register', methods=['POST'])
def register():
    """
    Registrar un nuevo usuario
    ---
    tags:
        - Users
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
            description: Usuario registrado exitosamente
        400:
            description: Usuario ya existe
    """
    # Obtener username y password del request
    username = request.json.get('username')
    password = request.json.get('password')
    # Llamar al servicio para registrar el usuario
    return services.register_user(username, password)

# Nuevo endpoint para actualizar un usuario
@bp.route('/update', methods=['PUT'])
def update():
    """
    Actualizar un usuario
    ---
    tags:
        - Users
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
            description: Usuario actualizado exitosamente
        400:
            description: Usuario no existe
    """
    # Obtener username y password del request
    username = request.json.get('username')
    password = request.json.get('password')
    # Llamar al servicio para actualizar el usuario
    return services.update_user(username, password)