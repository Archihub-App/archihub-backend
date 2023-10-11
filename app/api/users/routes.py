from app.api.users import bp
from flask import jsonify
from flask import request
from . import services
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity

# En este archivo se registran las rutas de la API para los usuarios

# Nuevo endpoint para obtener un usuario por id
@bp.route('/<id>', methods=['GET'])
@jwt_required()
def get_by_id(id):
    """
    Obtener un usuario por su id
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    responses:
        200:
            description: Usuario obtenido exitosamente
        400:
            description: Usuario no existe
        401:
            description: No tienes permisos para realizar esta acción
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not services.has_role(current_user, 'admin'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Llamar al servicio para obtener el usuario
    return services.get_by_id(id)

# Nuevo endpoint para registrar un usuario
@bp.route('/register', methods=['POST'])
@jwt_required()
def register():
    """
    Registrar un nuevo usuario
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
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
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not services.has_role(current_user, 'admin'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    
    body = request.json

    # Llamar al servicio para registrar el usuario
    return services.register_user(body, current_user)

# Nuevo endpoint para actualizar un usuario
@bp.route('/update', methods=['PUT'])
@jwt_required()
def update():
    """
    Actualizar un usuario
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                id:
                    type: string
            required:
                - id
    responses:
        200:
            description: Usuario actualizado exitosamente
        400:
            description: Usuario no existe
        401:
            description: No tienes permisos para realizar esta acción
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not services.has_role(current_user, 'admin'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    
    body = request.json

    # Llamar al servicio para actualizar el usuario
    return services.update_user(body, current_user)

# Nuevo endpoint para obtener el compromise de un usuario. Este compromise es el que el usuario acepta al iniciar sesión
@bp.route('/compromise', methods=['GET'])
@jwt_required()
def get_compromise():
    """
    Obtener el compromise de un usuario. Este es el mensaje que acepta el usuario al iniciar sesión.
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    responses:
        200:
            description: Compromise obtenido exitosamente
        400:
            description: Usuario no existe
    """
    current_user = get_jwt_identity()
    # Llamar al servicio para obtener el compromise del usuario
    user = services.get_user(current_user)
    # quitar el campo password del usuario
    user.pop('password')
    if not user:
        return jsonify({'msg': 'Usuario no existe'}), 400
    return user, 200

# Nuevo endpoint para aceptar el compromise de un usuario
@bp.route('/acceptcompromise', methods=['GET'])
@jwt_required()
def accept_compromise():
    """
    Aceptar el compromise de un usuario
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    responses:
        200:
            description: Compromise aceptado exitosamente
        400:
            description: Usuario no existe
    """
    current_user = get_jwt_identity()
    # Llamar al servicio para obtener el compromise del usuario
    user = services.get_user(current_user)
    # quitar el campo password del usuario
    user.pop('password')
    if not user:
        return jsonify({'msg': 'Usuario no existe'}), 400
    # Llamar al servicio para aceptar el compromise del usuario
    return services.accept_compromise(current_user)

# Nuevo endpoint para obtener un usuario por su username
@bp.route('/me', methods=['GET'])
@jwt_required()
def get_user():
    """
    Obtener un usuario por su header de autorización
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    responses:
        200:
            description: Usuario obtenido exitosamente
        400:
            description: Usuario no existe
    """
    current_user = get_jwt_identity()
    # Llamar al servicio para obtener el usuario
    user = services.get_user(current_user)
    # quitar el campo password del usuario
    user.pop('password')
    if not user:
        return jsonify({'msg': 'Usuario no existe'}), 400
    return user, 200

# Nuevo endpoint para obtener un token de acceso para un usuario
@bp.route('/token', methods=['GET'])
@jwt_required()
def get_token():
    """
    Obtener un token de acceso a la API pública para un usuario
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    responses:
        200:
            description: Token obtenido exitosamente
        400:
            description: Usuario no existe, no ha aceptado el compromise o no tiene token de acceso
    """
    current_user = get_jwt_identity()
    # Llamar al servicio para obtener el token
    return services.get_token(current_user)

# Nuevo endpoint POST con un username y password en el body para generar un token de acceso para un usuario
@bp.route('/token', methods=['POST'])
@jwt_required()
def generate_token():
    """
    Generar un token de acceso a la API pública para un usuario
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                password:
                    type: string
            required:
                - password
    responses:
        200:
            description: Token generado exitosamente
        400:
            description: Usuario no existe o contraseña incorrecta
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Obtener el body del request
    body = request.json
    # Obtener la contraseña del body
    password = body.get('password')
    # Llamar al servicio para generar el token
    return services.generate_token(current_user, password)

# Nuevo endpoint para generar un token de acceso para un usuario admin
@bp.route('/admin-token', methods=['POST'])
@jwt_required()
def generate_admin_token():
    """
    Generar un token de acceso a la API para un usuario admin
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                password:
                    type: string
            required:
                - password
    responses:
        200:
            description: Token generado exitosamente
        400:
            description: Usuario no existe o contraseña incorrecta
        401:
            description: No tienes permisos para realizar esta acción
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not services.has_role(current_user, 'admin'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Obtener el body del request
    body = request.json
    # Obtener el username y password del body
    password = body.get('password')
    # Llamar al servicio para generar el token
    return services.generate_token(current_user, password, True)

# Nuevo endpoint para obtener todos los usuarios usando filtros
@bp.route('', methods=['POST'])
@jwt_required()
def get_all():
    """
    Obtener todos los usuarios usando filtros
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                filters:
                    type: object
                sort:
                    type: string
                limit:
                    type: integer
                skip:
                    type: integer
    responses:
        200:
            description: Usuarios obtenidos exitosamente
        401:
            description: No tienes permisos para realizar esta acción
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not services.has_role(current_user, 'admin'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Obtener el body del request
    body = request.json
    # Llamar al servicio para obtener los usuarios
    return services.get_all(body, current_user)

# Nuevo endpoint para obtener la cantidad de requests por usuario y el lastRequest
@bp.route('/requests', methods=['GET'])
@jwt_required()
def get_requests():
    """
    Obtener la cantidad de requests por usuario y el lastRequest
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    responses:
        200:
            description: Requests obtenidos exitosamente
        401:
            description: No tienes permisos para realizar esta acción
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    # Llamar al servicio para obtener los requests
    return services.get_requests(current_user)