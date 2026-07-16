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
    Registrar un nuevo usuario (requiere rol admin)
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
                name:
                    type: string
                username:
                    type: string
                    description: Debe tener formato de email
                password:
                    type: string
                confirmPassword:
                    type: string
                    description: Debe coincidir con password
                roles:
                    type: array
                    items:
                        type: object
                        properties:
                            id:
                                type: string
                    description: Cada id debe existir en el listado de roles del sistema
                accessRights:
                    type: array
                    items:
                        type: object
                        properties:
                            id:
                                type: string
                    description: Cada id debe existir en el listado de niveles de acceso del sistema
            required:
                - name
                - username
                - password
                - confirmPassword
                - roles
                - accessRights
    responses:
        201:
            description: Usuario registrado exitosamente
        400:
            description: El usuario ya existe, un rol/accessRight no existe, o error de validación de campos (ver errors en el body de la respuesta)
        401:
            description: No tienes permisos para realizar esta acción (se requiere rol admin)
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
    Auto-registro público de un nuevo usuario (rol 'user' fijo, sin accessRights). Requiere que el ajuste
    'user_management' del sistema tenga habilitado el registro público, y envía un correo de verificación
    de cuenta con un token de un día de validez
    ---
    tags:
        - Usuarios
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
                    description: Debe tener formato de email
                password:
                    type: string
                confirmPassword:
                    type: string
                    description: Debe coincidir con password
            required:
                - name
                - username
                - password
                - confirmPassword
    responses:
        201:
            description: Usuario registrado exitosamente, pendiente de verificación por correo
        400:
            description: El auto-registro está deshabilitado, el usuario ya existe, o error de validación de campos (ver errors en el body de la respuesta)
        500:
            description: Error registrando el usuario
    """
    # Obtener el usuario actual
    body = request.json

    # Llamar al servicio para registrar el usuario
    return services.register_me(body)

@bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    """
    Solicitar recuperación de contraseña por correo. Requiere que el ajuste 'user_management' del sistema
    tenga habilitada la recuperación de contraseña; envía un enlace de restablecimiento válido por un día
    ---
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
            description: Correo de recuperación enviado exitosamente
        400:
            description: La recuperación de contraseña está deshabilitada
        404:
            description: El usuario no existe
        500:
            description: Error en el servidor
    """
    body = request.json

    # Llamar al servicio para registrar el usuario
    return services.forgot_password(body)

# Nuevo endpoint para actualizar un usuario
@bp.route('/update', methods=['PUT'])
@jwt_required()
def update():
    """
    Actualizar un usuario existente (requiere rol admin). El username no se puede cambiar; cualquier otro
    campo de UserUpdate (name, password, photo, roles, accessRights, etc.) puede incluirse en el body
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
                _id:
                    type: string
                    description: id del usuario a actualizar
                username:
                    type: string
                    description: Debe coincidir con el username actual del usuario; no se puede modificar
                roles:
                    type: array
                    items:
                        type: object
                        properties:
                            id:
                                type: string
                    description: Cada id debe existir en el listado de roles del sistema; debe incluir al menos uno de user/editor/admin
                accessRights:
                    type: array
                    items:
                        type: object
                        properties:
                            id:
                                type: string
                    description: Cada id debe existir en el listado de niveles de acceso del sistema
            required:
                - _id
                - username
                - roles
                - accessRights
    responses:
        200:
            description: Usuario actualizado exitosamente
        400:
            description: El usuario no existe, se intentó cambiar el username, no se incluyó ningún rol de sistema (user/editor/admin), o un rol/accessRight no existe
        401:
            description: No tienes permisos para realizar esta acción (se requiere rol admin)
        500:
            description: Error en el servidor
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
    Eliminar un usuario por su username (requiere rol admin). Un usuario no se puede eliminar a sí mismo
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
            description: Usuario eliminado exitosamente
        400:
            description: Intentaste eliminar tu propio usuario
        401:
            description: No tienes permisos para realizar esta acción (se requiere rol admin)
        404:
            description: El usuario no existe
        500:
            description: Error en el servidor
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
    Actualizar el propio perfil (nombre y/o contraseña) del usuario autenticado. Requiere confirmar la
    contraseña actual; para no cambiar la contraseña, envía new_password y new_password_confirmation vacíos
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
                    description: Contraseña actual, para confirmar la identidad del usuario
                name:
                    type: string
                new_password:
                    type: string
                    description: Nueva contraseña; usar cadena vacía para no cambiarla
                new_password_confirmation:
                    type: string
                    description: Debe coincidir con new_password; usar cadena vacía para no cambiarla
            required:
                - password
                - name
                - new_password
                - new_password_confirmation
    responses:
        200:
            description: Usuario actualizado exitosamente
        400:
            description: Contraseña actual incorrecta, las nuevas contraseñas no coinciden, o no se modificó ningún campo
        404:
            description: El usuario no existe
        500:
            description: Error en el servidor
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
    Obtener los datos completos del usuario autenticado (identificado por el JWT), incluyendo si ya aceptó
    el compromiso que se muestra al iniciar sesión. Nota: a diferencia de /me, este endpoint no elimina el
    campo password (hash bcrypt) del objeto devuelto
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    responses:
        200:
            description: Usuario obtenido exitosamente
        400:
            description: El usuario no existe o no está verificado
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
    Aceptar, para el usuario autenticado, el compromiso que se muestra al iniciar sesión
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    responses:
        200:
            description: Compromiso aceptado exitosamente
        400:
            description: El usuario no existe o no está verificado
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
    Obtener los datos del usuario autenticado (identificado por el JWT), sin el campo password
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    responses:
        200:
            description: Usuario obtenido exitosamente
        500:
            description: Error en el servidor. Nota- si el usuario no existe o no está verificado, el código actual intenta hacer user.pop('password') sobre None antes de comprobar si el usuario existe, lo que produce un error 500 en vez del 400 documentado originalmente
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
    Generar (y persistir, cifrado con Fernet, en el campo token del usuario) un token de acceso sin
    expiración para el usuario autenticado, usado por la API pública
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
            description: Token generado exitosamente (access_token en la respuesta)
        400:
            description: Usuario no existe o contraseña incorrecta
        500:
            description: Error en el servidor (p. ej. si no se envía password en el body)
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
    Generar (y persistir, cifrado con Fernet, en el campo adminToken del usuario) un token de acceso a la
    API para el usuario admin autenticado, con expiración configurable (requiere rol admin)
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
                duration:
                    description: Días de validez del token. false para que no expire. Por defecto 2
                    type: integer
            required:
                - password
    responses:
        200:
            description: Token generado exitosamente (access_token en la respuesta)
        400:
            description: No se envió password, duration no es un entero ni false, o la contraseña es incorrecta
        401:
            description: No tienes permisos para realizar esta acción (se requiere rol admin)
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
    Generar (y persistir, cifrado con Fernet, en el campo nodeToken del usuario) un token de acceso sin
    expiración para los nodos de procesamiento, para el usuario admin autenticado (requiere rol admin)
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
            description: Token generado exitosamente (access_token en la respuesta)
        400:
            description: Usuario no existe o contraseña incorrecta
        401:
            description: No tienes permisos para realizar esta acción (se requiere rol admin)
        500:
            description: Error en el servidor (p. ej. si no se envía password en el body)
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
    Generar (y persistir, cifrado con Fernet, en el campo vizToken del usuario) un token de acceso sin
    expiración para el visualizador/dashboard, para el usuario autenticado (requiere rol visualizer)
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
            description: Token generado exitosamente (access_token en la respuesta)
        400:
            description: Usuario no existe o contraseña incorrecta
        401:
            description: No tienes permisos para realizar esta acción (se requiere rol visualizer)
        500:
            description: Error en el servidor (p. ej. si no se envía password en el body)
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
    Obtener usuarios paginados (20 por página, ordenados por nombre) usando filtros, sin exponer campos
    sensibles (password, status, photo, compromise, token, adminToken, nodeToken). Requiere rol admin o
    editor
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
                    description: Filtro Mongo aplicado a la colección users. Por defecto {}
                page:
                    type: integer
                    description: Página de resultados (20 por página). Por defecto 0
    responses:
        200:
            description: Usuarios obtenidos exitosamente (incluye el total en cada resultado)
        401:
            description: No tienes permisos para realizar esta acción (se requiere rol admin o editor)
        500:
            description: Error en el servidor
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
    Obtener la cantidad de requests de la semana actual y el lastRequest del usuario autenticado. Si la
    última request registrada no es de la semana en curso, el contador se reinicia a 0 antes de devolverlo
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    responses:
        200:
            description: Requests obtenidos exitosamente
        404:
            description: El usuario no existe
        500:
            description: Error en el servidor
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
    Agregar un favorito para el usuario autenticado. type es el nombre literal de la colección de Mongo
    donde vive el elemento (p. ej. 'resources', 'records'), no un slug de content-type. Si type es
    'resources', el recurso debe estar publicado
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
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
                    description: Nombre de la colección Mongo del elemento (p. ej. 'resources', 'records')
                view:
                    type: string
    responses:
        200:
            description: Favorito agregado exitosamente
        400:
            description: El recurso existe pero no está publicado (solo aplica cuando type es 'resources')
        404:
            description: El elemento referenciado por id/type no existe
        500:
            description: Error en el servidor
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
    Eliminar un favorito del usuario autenticado. No valida que el favorito exista previamente: siempre
    responde 200, incluso si el par id/type no estaba en la lista de favoritos
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
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
                    description: Nombre de la colección Mongo del elemento (p. ej. 'resources', 'records')
    responses:
        200:
            description: Favorito eliminado exitosamente
        500:
            description: Error en el servidor
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
    Obtener, paginados (20 por página), los favoritos del usuario autenticado de un type (colección Mongo)
    dado. Si type es 'resources' solo se devuelven los que siguen publicados
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    parameters:
        - in: body
          name: body
          type: object
          required: true
          properties:
                type:
                    type: string
                    description: Nombre de la colección Mongo a filtrar (p. ej. 'resources', 'records'). Obligatorio
                page:
                    type: integer
                    description: Página de resultados (20 por página). Obligatorio
    responses:
        200:
            description: Favoritos obtenidos exitosamente (total y results)
        500:
            description: Error en el servidor (p. ej. si falta type o page en el body)
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
    Obtener los snaps del usuario autenticado de un type dado, paginados
    ---
    security:
        - JWT: []
    tags:
        - Usuarios
    parameters:
        - in: body
          name: body
          type: object
          required: true
          properties:
                type:
                    type: string
                    description: Obligatorio
                page:
                    type: integer
                    description: Obligatorio
    responses:
        200:
            description: Snaps obtenidos exitosamente
        400:
            description: Falta el campo type o page en el body
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