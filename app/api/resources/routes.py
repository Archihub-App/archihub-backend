from app.api.resources import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.resources import services
from app.api.users import services as user_services
from flask import request, jsonify


# En este archivo se registran las rutas de la API para los recursos

# Nuevo endpoint para obtener todos los recursos dado un tipo de contenido y un body de filtros
@bp.route('/<post_type>', methods=['POST'])
@jwt_required()
def get_all(post_type):
    """
    Obtener todos los recursos dado un tipo de contenido y un body de filtros
    ---
    security:
        - JWT: []
    tags:
        - Recursos
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
            description: Recursos obtenidos exitosamente
        401:
            description: No tiene permisos para obtener los recursos
        500:
            description: Error al obtener los recursos
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Obtener el body del request
    body = request.json
    # Llamar al servicio para obtener los recursos
    return services.get_all(post_type, body, current_user)

# Nuevo endpoint para guardar un recurso nuevo
@bp.route('', methods=['POST'])
@jwt_required()
def create():
    """
    Crear un recurso nuevo con el body del request y agrega al contador de recursos del tipo de contenido
    ---
    security:
        - JWT: []
    tags:
        - Recursos
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                post_type:
                    type: string
                metadata:
                    type: object
                files:
                    type: array
                    items:
                        type: object
                        properties:
                            name:
                                type: string
                            file:
                                type: string
                ident:
                    type: string

    responses:
        201:
            description: Recurso creado exitosamente
        401:
            description: No tiene permisos para crear un recurso
        500:
            description: Error al crear el recurso
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Obtener el body del request
    body = request.json

    # Llamar al servicio para crear el recurso
    return services.create(body, current_user)

# Nuevo endpoint para obtener un recurso por su id
@bp.route('/<id>', methods=['GET'])
@jwt_required()
def get_by_id(id):
    """
    Obtener un recurso por su id
    ---
    security:
        - JWT: []
    tags:
        - Recursos
    parameters:
        - in: path
          name: id
          schema:
            type: string
    responses:
        200:
            description: Recurso obtenido exitosamente
        401:
            description: No tiene permisos para obtener el recurso
        500:
            description: Error al obtener el recurso
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Llamar al servicio para obtener el recurso
    return services.get_by_id(id, current_user)

# Nuevo endpoint para actualizar un recurso por su id
@bp.route('/<id>', methods=['PUT'])
@jwt_required()
def update_by_id(id):
    """
    Actualizar un recurso por su id
    ---
    security:
        - JWT: []
    tags:
        - Recursos
    parameters:
        - in: path
          name: id
          schema:
            type: string
        - in: body
          name: body
          schema:
            type: object
            properties:
                metadata:
                    type: object
                files:
                    type: array
                    items:
                        type: object
                        properties:
                            name:
                                type: string
                            file:
                                type: string
                ident:
                    type: string
    responses:
        200:
            description: Recurso actualizado exitosamente
        401:
            description: No tiene permisos para actualizar el recurso
        500:
            description: Error al actualizar el recurso
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Obtener el body del request
    body = request.json
    # Llamar al servicio para actualizar el recurso
    return services.update_by_id(id, body, current_user)

# Nuevo endpoint para eliminar un recurso por su id
@bp.route('/<id>', methods=['DELETE'])
@jwt_required()
def delete_by_id(id):
    """
    Eliminar un recurso por su id
    ---
    security:
        - JWT: []
    tags:
        - Recursos
    parameters:
        - in: path
          name: id
          schema:
            type: string
    responses:
        200:
            description: Recurso eliminado exitosamente
        401:
            description: No tiene permisos para eliminar el recurso
        500:
            description: Error al eliminar el recurso
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Llamar al servicio para eliminar el recurso
    return services.delete_by_id(id, current_user)