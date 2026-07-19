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
    Get a user by their id
    ---
    security:
        - JWT: []
    tags:
        - Users
    parameters:
        - in: path
          name: id
          type: string
          required: true
    responses:
        200:
            description: User retrieved successfully
        401:
            description: You don't have permission to perform this action
        404:
            description: User does not exist
        500:
            description: Error retrieving the user
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
    Register a new user (requires admin role)
    ---
    security:
        - JWT: []
    tags:
        - Users
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
                username:
                    type: string
                    description: Must be in email format
                password:
                    type: string
                confirmPassword:
                    type: string
                    description: Must match password
                roles:
                    type: array
                    items:
                        type: object
                        properties:
                            id:
                                type: string
                    description: Each id must exist in the system's role list
                accessRights:
                    type: array
                    items:
                        type: object
                        properties:
                            id:
                                type: string
                    description: Each id must exist in the system's access-level list
            required:
                - name
                - username
                - password
                - confirmPassword
                - roles
                - accessRights
    responses:
        201:
            description: User registered successfully
        400:
            description: The user already exists, a role/accessRight doesn't exist, or field validation error (see errors in the response body)
        401:
            description: You don't have permission to perform this action (admin role required)
        500:
            description: Error registering the user
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
    Public self-registration of a new user (fixed 'user' role, no accessRights). Requires the system's
    'user_management' setting to have public registration enabled, and sends an account verification
    email with a token valid for one day
    ---
    tags:
        - Users
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
                username:
                    type: string
                    description: Must be in email format
                password:
                    type: string
                confirmPassword:
                    type: string
                    description: Must match password
            required:
                - name
                - username
                - password
                - confirmPassword
    responses:
        201:
            description: User registered successfully, pending email verification
        400:
            description: Self-registration is disabled, the user already exists, or field validation error (see errors in the response body)
        500:
            description: Error registering the user
    """
    # Obtener el usuario actual
    body = request.json

    # Llamar al servicio para registrar el usuario
    return services.register_me(body)

@bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    """
    Request password recovery by email. Requires the system's 'user_management' setting to have password
    recovery enabled; sends a reset link valid for one day
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
            required:
                - username
    responses:
        200:
            description: Recovery email sent successfully
        400:
            description: Password recovery is disabled
        404:
            description: User does not exist
        500:
            description: Server error
    """
    body = request.json

    # Llamar al servicio para registrar el usuario
    return services.forgot_password(body)

# Nuevo endpoint para actualizar un usuario
@bp.route('/update', methods=['PUT'])
@jwt_required()
def update():
    """
    Update an existing user (requires admin role). The username cannot be changed; any other
    UserUpdate field (name, password, photo, roles, accessRights, etc.) can be included in the body
    ---
    security:
        - JWT: []
    tags:
        - Users
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                _id:
                    type: string
                    description: id of the user to update
                username:
                    type: string
                    description: Must match the user's current username; cannot be changed
                roles:
                    type: array
                    items:
                        type: object
                        properties:
                            id:
                                type: string
                    description: Each id must exist in the system's role list; must include at least one of user/editor/admin
                accessRights:
                    type: array
                    items:
                        type: object
                        properties:
                            id:
                                type: string
                    description: Each id must exist in the system's access-level list
            required:
                - _id
                - username
                - roles
                - accessRights
    responses:
        200:
            description: User updated successfully
        400:
            description: The user doesn't exist, an attempt was made to change the username, no system role (user/editor/admin) was included, or a role/accessRight doesn't exist
        401:
            description: You don't have permission to perform this action (admin role required)
        500:
            description: Server error
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
    Delete a user by their username (requires admin role). A user cannot delete themselves
    ---
    security:
        - JWT: []
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
            required:
                - username
    responses:
        200:
            description: User deleted successfully
        400:
            description: You tried to delete your own user
        401:
            description: You don't have permission to perform this action (admin role required)
        404:
            description: User does not exist
        500:
            description: Server error
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
    Update the authenticated user's own profile (name and/or password). Requires confirming the
    current password; to leave the password unchanged, send empty new_password and new_password_confirmation
    ---
    security:
        - JWT: []
    tags:
        - Users
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                password:
                    type: string
                    description: Current password, to confirm the user's identity
                name:
                    type: string
                new_password:
                    type: string
                    description: New password; use an empty string to leave it unchanged
                new_password_confirmation:
                    type: string
                    description: Must match new_password; use an empty string to leave it unchanged
            required:
                - password
                - name
                - new_password
                - new_password_confirmation
    responses:
        200:
            description: User updated successfully
        400:
            description: Incorrect current password, the new passwords don't match, or no field was modified
        404:
            description: User does not exist
        500:
            description: Server error
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
    Get the full data of the authenticated user (identified by the JWT), including whether they've already
    accepted the commitment shown at login. Note: unlike /me, this endpoint does not remove the
    password field (bcrypt hash) from the returned object
    ---
    security:
        - JWT: []
    tags:
        - Users
    responses:
        200:
            description: User retrieved successfully
        400:
            description: The user doesn't exist or isn't verified
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
    Accept, for the authenticated user, the commitment shown at login
    ---
    security:
        - JWT: []
    tags:
        - Users
    responses:
        200:
            description: Commitment accepted successfully
        400:
            description: The user doesn't exist or isn't verified
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
    Get the authenticated user's data (identified by the JWT), without the password field
    ---
    security:
        - JWT: []
    tags:
        - Users
    responses:
        200:
            description: User retrieved successfully
        500:
            description: "Server error. Note: if the user doesn't exist or isn't verified, the current code tries to call user.pop('password') on None before checking whether the user exists, which produces a 500 error instead of the originally documented 400"
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
    Generate (and persist, Fernet-encrypted, in the user's token field) a non-expiring
    access token for the authenticated user, used by the public API
    ---
    security:
        - JWT: []
    tags:
        - Users
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
            description: Token generated successfully (access_token in the response)
        400:
            description: User does not exist or incorrect password
        500:
            description: Server error (e.g. if password is not sent in the body)
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
    Generate (and persist, Fernet-encrypted, in the user's adminToken field) an API access
    token for the authenticated admin user, with configurable expiration (requires admin role)
    ---
    security:
        - JWT: []
    tags:
        - Users
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                password:
                    type: string
                duration:
                    description: Days the token is valid for. false for it not to expire. Defaults to 2
                    type: integer
            required:
                - password
    responses:
        200:
            description: Token generated successfully (access_token in the response)
        400:
            description: password was not sent, duration is neither an integer nor false, or the password is incorrect
        401:
            description: You don't have permission to perform this action (admin role required)
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
    Generate (and persist, Fernet-encrypted, in the user's nodeToken field) a non-expiring
    access token for the processing nodes, for the authenticated admin user (requires admin role)
    ---
    security:
        - JWT: []
    tags:
        - Users
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
            description: Token generated successfully (access_token in the response)
        400:
            description: User does not exist or incorrect password
        401:
            description: You don't have permission to perform this action (admin role required)
        500:
            description: Server error (e.g. if password is not sent in the body)
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
    Generate (and persist, Fernet-encrypted, in the user's vizToken field) a non-expiring
    access token for the visualizer/dashboard, for the authenticated user (requires visualizer role)
    ---
    security:
        - JWT: []
    tags:
        - Users
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
            description: Token generated successfully (access_token in the response)
        400:
            description: User does not exist or incorrect password
        401:
            description: You don't have permission to perform this action (visualizer role required)
        500:
            description: Server error (e.g. if password is not sent in the body)
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
    Get paginated users (20 per page, sorted by name) using filters, without exposing sensitive
    fields (password, status, photo, compromise, token, adminToken, nodeToken). Requires admin or
    editor role
    ---
    security:
        - JWT: []
    tags:
        - Users
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                filters:
                    type: object
                    description: Mongo filter applied to the users collection. Defaults to {}
                page:
                    type: integer
                    description: Results page (20 per page). Defaults to 0
    responses:
        200:
            description: Users retrieved successfully (includes the total in each result)
        401:
            description: You don't have permission to perform this action (admin or editor role required)
        500:
            description: Server error
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
    Get the current week's request count and lastRequest for the authenticated user. If the
    last recorded request is not from the current week, the counter is reset to 0 before returning it
    ---
    security:
        - JWT: []
    tags:
        - Users
    responses:
        200:
            description: Requests retrieved successfully
        404:
            description: The user does not exist
        500:
            description: Server error
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
    Add a favorite for the authenticated user. type is the literal name of the Mongo collection
    where the item lives (e.g. 'resources', 'records'), not a content-type slug. If type is
    'resources', the resource must be published
    ---
    security:
        - JWT: []
    tags:
        - Users
    parameters:
        - in: body
          name: body
          type: object
          required: true
          properties:
                id:
                    type: string
                type:
                    type: string
                    description: Mongo collection name of the item (e.g. 'resources', 'records')
                view:
                    type: string
    responses:
        200:
            description: Favorite added successfully
        400:
            description: The resource exists but is not published (only applies when type is 'resources')
        404:
            description: The item referenced by id/type does not exist
        500:
            description: Server error
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
    Delete a favorite of the authenticated user. Does not validate that the favorite previously
    existed: always responds 200, even if the id/type pair was not in the favorites list
    ---
    security:
        - JWT: []
    tags:
        - Users
    parameters:
        - in: body
          name: body
          type: object
          required: true
          properties:
                id:
                    type: string
                type:
                    type: string
                    description: Mongo collection name of the item (e.g. 'resources', 'records')
    responses:
        200:
            description: Favorite deleted successfully
        500:
            description: Server error
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
    Get, paginated (20 per page), the authenticated user's favorites of a given type (Mongo
    collection). If type is 'resources' only the ones that are still published are returned
    ---
    security:
        - JWT: []
    tags:
        - Users
    parameters:
        - in: body
          name: body
          type: object
          required: true
          properties:
                type:
                    type: string
                    description: Mongo collection name to filter by (e.g. 'resources', 'records'). Required
                page:
                    type: integer
                    description: Results page (20 per page). Required
    responses:
        200:
            description: Favorites retrieved successfully (total and results)
        500:
            description: Server error (e.g. if type or page is missing from the body)
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
    Get the authenticated user's snaps of a given type, paginated
    ---
    security:
        - JWT: []
    tags:
        - Users
    parameters:
        - in: body
          name: body
          type: object
          required: true
          properties:
                type:
                    type: string
                    description: Required
                page:
                    type: integer
                    description: Required
    responses:
        200:
            description: Snaps retrieved successfully
        400:
            description: Missing type or page field in the body
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