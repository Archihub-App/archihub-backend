from app.api.lists import bp
from flask import jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.api.lists import services
from app.api.users import services as user_services

# En este archivo se registran las rutas de la API para los listados cerrados

# Nuevo endpoint para obtener todos los listados
@bp.route('', methods=['GET'])
@jwt_required()
def get_all():
    """
    Obtener todos los listados de la base de datos
    ---
    security:
        - JWT: []
    tags:
        - Listados
    responses:
        200:
            description: Lista de listados obtenida exitosamente
        401:
            description: No tienes permisos para realizar esta acción
        500:
            description: Error al obtener los listados
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Llamar al servicio para obtener todos los listados
    resp = services.get_all()

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

# Nuevo endpoint para crear un listado
@bp.route('', methods=['POST'])
@jwt_required()
def create():
    """
    Crear un listado nuevo con el body del request
    ---
    security:
        - JWT: []
    tags:
        - Listados
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
                description:
                    type: string
                slug:
                    type: string
                fields:
                    type: array
                    items:
                        type: object
            required:
                - name
                - description
    responses:
        201:
            description: Listado creado exitosamente
        400:
            description: Error al crear el listado
        401:
            description: No tienes permisos para realizar esta acción
        500:
            description: Error al crear el listado
    """
    # Obtener el body de la request
    body = request.json

    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    
    # Llamar al servicio para crear un listado nuevo
    return services.create(body, current_user)

# Nuevo endpoint para devolver un estándar por su slug
@bp.route('/<id>', methods=['GET'])
@jwt_required()
def get_by_id(id):
    """
    Obtener un estándar por su id
    ---
    security:
        - JWT: []
    tags:
        - Listados
    parameters:
        - in: path
          name: id
          type: string
          required: true
    responses:
        200:
            description: Listado obtenido exitosamente
        401:
            description: No tienes permisos para realizar esta acción
        404:
            description: Listado no encontrado
        500:
            description: Error al obtener el listado
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Llamar al servicio para obtener el listado por su id
    resp = services.get_by_id(id)

    # Si el listado no existe, retornar error
    if 'msg' in resp:
        if resp['msg'] == 'Listado no existe':
            return jsonify(resp), 404
    # Retornar el listado
    return jsonify(resp), 200

# Nuevo endpoint para actualizar un listado por su slug
@bp.route('/<id>', methods=['PUT'])
@jwt_required()
def update_by_id(id):
    """
    Actualizar un listado por su id
    ---
    security:
        - JWT: []
    tags:
        - Listados
    parameters:
        - in: path
          name: slug
          schema:
            type: string
          required: true
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
                description:
                    type: string
                options:
                    type: array
                    items:
                        type: object

            required:
                - name
    responses:
        200:
            description: Listado actualizado exitosamente
        400:
            description: Error al actualizar el listado
        401:
            description: No tienes permisos para realizar esta acción
        404:
            description: Listado no encontrado
        500:
            description: Error al actualizar el listado
    """
    
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # parseamos el body
    try:
        body = request.json
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 400
    # Llamar al servicio para actualizar el estándar por su slug
    return services.update_by_id(id, body, current_user)

# Nuevo endpoint para eliminar un listado por su slug
@bp.route('/<id>', methods=['DELETE'])
@jwt_required()
def delete_by_id(id):
    """
    Eliminar un listado por su slug
    ---
    security:
        - JWT: []
    tags:
        - Listados
    parameters:
        - in: path
          name: slug
          schema:
            type: string
          required: true
    responses:
        200:
            description: Listado eliminado exitosamente
        401:
            description: No tienes permisos para realizar esta acción
        404:
            description: Listado no encontrado
        500:
            description: Error al eliminar el listado
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Llamar al servicio para eliminar el listado por su slug
    return services.delete_by_id(id, current_user)