from app.api.users import bp
from flask import jsonify
from flask import request
from . import services
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from flask_babel import _

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
    parameters:
        - in: path
          name: id
          type: string
          required: true
    responses:
        200:
            description: Usuario obtenido exitosamente
        401:
            description: No tienes permisos para realizar esta acción
        404:
            description: Usuario no existe
        500:
            description: Error obteniendo el usuario
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para obtener el usuario
    resp = services.get_by_id(id)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

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
        201:
            description: Usuario registrado exitosamente
        400:
            description: Usuario ya existe
        500:
            description: Error registrando el usuario
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    
    body = request.json

    # Llamar al servicio para registrar el usuario
    return services.register_user(body)

@bp.route('/register-me', methods=['POST'])
def registerme():
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
        201:
            description: Usuario registrado exitosamente
        400:
            description: Usuario ya existe
    """
    # Obtener el usuario actual
    body = request.json

    # Llamar al servicio para registrar el usuario
    return services.register_me(body)

@bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    """
    Olvidé mi contraseña
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
            required:
                - username
    responses:
        200:
            description: Correo enviado exitosamente
        400:
            description: Usuario no existe
    """
    body = request.json

    # Llamar al servicio para registrar el usuario
    return services.forgot_password(body)

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
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    
    body = request.json

    # Llamar al servicio para actualizar el usuario
    return services.update_user(body, current_user)

@bp.route('/delete', methods=['DELETE'])
@jwt_required()
def delete():
    """
    Eliminar un usuario
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
            description: Usuario eliminado exitosamente
        400:
            description: Usuario no existe
        401:
            description: No tienes permisos para realizar esta acción
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    
    body = request.json

    # Llamar al servicio para eliminar el usuario
    return services.delete_user(body, current_user)

@bp.route('/update-me', methods=['PUT'])
@jwt_required()
def updateme():
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
    body = request.json
    # Llamar al servicio para actualizar el usuario
    return services.update_me(body, current_user)

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
    parameters:
        - in: path
          name: Usuario actual
          type: string
          required: true
    responses:
        200:
            description: Compromise obtenido exitosamente
        400:
            description: Usuario no existe
    """
    current_user = get_jwt_identity()
    # Llamar al servicio para obtener el compromise del usuario
    user = services.get_user(current_user)

    if not user:
        return jsonify({'msg': _('User does not exist')}), 400
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
    parameters:
        - in: path
          name: Usuario actual
          type: string
          required: true
    responses:
        200:
            description: Compromise aceptado exitosamente
        400:
            description: Usuario no existe
    """
    current_user = get_jwt_identity()
    # Llamar al servicio para obtener el compromise del usuario
    user = services.get_user(current_user)
    
    if not user:
        return jsonify({'msg': _('User does not exist')}), 400
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
    parameters:
        - in: path
          name: Usuario actual
          type: string
          required: true
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
        return jsonify({'msg': _('User does not exist')}), 400
    return user, 200

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
    # Obtener el body del request
    body = request.json
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    if 'password' not in body:
        return jsonify({'msg': _('You must specify the password of the user')}), 400
    if 'duration' not in request.json:
        body['duration'] = 2
    if not isinstance(body['duration'], int) and body['duration'] != False:
        return jsonify({'msg': _('Duration must be an integer or false')}), 400
    
    # Llamar al servicio para generar el token
    return services.generate_token(current_user, body['password'], True, body['duration'])

@bp.route('/node-token', methods=['POST'])
@jwt_required()
def generate_node_token():
    
    """
    Generar un token de acceso a la API para los nodos de procesamiento
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
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.json
    # Obtener el username y password del body
    password = body.get('password')
    # Llamar al servicio para generar el token
    return services.generate_node_token(current_user, password)

@bp.route('/viz-token', methods=['POST'])
@jwt_required()
def generate_viz_token():
    """
    Generar un token de acceso a la API para los nodos de procesamiento
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
    if not services.has_role(current_user, 'visualizer'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.json
    # Obtener el username y password del body
    password = body.get('password')
    # Llamar al servicio para generar el token
    return services.generate_viz_token(current_user, password)

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
    if not services.has_role(current_user, 'admin') and not services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.json
    
    print(body)
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
    parameters:
        - in: path
          name: Usuario actual
          type: string
          required: true
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

# Nuevo endpoint para obtener la cantidad de requests por usuario y el lastRequest
@bp.route('/favorites', methods=['POST'])
@jwt_required()
def set_favorite():
    """
    Agregar un favorito a un usuario
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    parameters:
        - in: path
          name: Usuario actual
          type: string
          required: true
        - in: body
          name: body
          type: object
          required: true
          properties:
                id:
                    type: string
                type:
                    type: string
                view:
                    type: string
    responses:
        200:
            description: Requests obtenidos exitosamente
        401:
            description: No tienes permisos para realizar esta acción
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    body = request.json
    
    # Llamar al servicio para obtener los requests
    return services.set_favorite(current_user, body)

@bp.route('/favorites', methods=['DELETE'])
@jwt_required()
def delete_favorite():
    """
    Eliminar un favorito de un usuario
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    parameters:
        - in: path
          name: Usuario actual
          type: string
          required: true
        - in: body
          name: body
          type: object
          required: true
          properties:
                id:
                    type: string
                type:
                    type: string
    responses:
        200:
            description: Requests obtenidos exitosamente
        401:
            description: No tienes permisos para realizar esta acción
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    body = request.json
    
    # Llamar al servicio para obtener los requests
    return services.delete_favorite(current_user, body)

# Nuevo endpoint para obtener los favoritos de un usuario paginados
@bp.route('/favorites_list', methods=['POST'])
@jwt_required()
def get_favorites():
    """
    Obtener los favoritos de un usuario paginados
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    parameters:
        - in: path
          name: Usuario actual
          type: string
          required: true
        - in: body
          name: body
          type: object
          required: true
          properties:
                type:
                    type: string
                page:
                    type: integer
    responses:
        200:
            description: Favoritos obtenidos exitosamente
        401:
            description: No tienes permisos para realizar esta acción
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Obtener el body del request
    body = request.json
    # Llamar al servicio para obtener los favoritos
    return services.get_favorites(current_user, body)

@bp.route('/snaps', methods=['POST'])
@jwt_required()
def get_snaps():
    """
    Obtener los snaps de un usuario
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    parameters:
        - in: path
          name: Usuario actual
          type: string
          required: true
        - in: body
          name: body
          type: object
          required: true
          properties:
                type:
                    type: string
                page:
                    type: integer
    responses:
        200:
            description: Snaps obtenidos exitosamente
        401:
            description: No tienes permisos para realizar esta acción
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    body = request.json

    if 'type' not in body:
        return jsonify({'msg': _('Missing type field in body')}), 400
    if 'page' not in body:
        return jsonify({'msg': _('Missing page field in body')}), 400

    # Llamar al servicio para obtener los snaps
    from app.api.snaps.services import get_by_user_id
    resp = get_by_user_id(current_user, body)
    
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp