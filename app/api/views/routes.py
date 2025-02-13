from app.api.views import bp
from flask_jwt_extended import jwt_required, get_jwt_identity
from flask import jsonify, request
from app.api.views import services
from app.api.users import services as user_services
from flask_babel import _

@bp.route('/<view_id>', methods=['GET'])
@jwt_required()
def get_view(view_id):
    """
    Obtener una vista de consulta
    ---
    tags:
        - Vistas
    parameters:
        - in: path
          name: view_id
          type: string
          required: true
    responses:
        200:
            description: Retorna la vista de consulta
        401:
            description: No tienes permisos para realizar esta acción
        500:
            description: Error al obtener la vista de consulta
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para obtener una vista de consulta
    resp = services.get(view_id, current_user)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/<view_id>', methods=['PUT'])
@jwt_required()
def update_view(view_id):
    """
    Actualizar una vista de consulta
    ---
    tags:
        - Vistas
    parameters:
        - in: path
          name: view_id
          type: string
          required: true
    responses:
        200:
            description: Vista de consulta actualizada exitosamente
        401:
            description: No tienes permisos para realizar esta acción
        500:
            description: Error al actualizar la vista de consulta
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.json
    # Llamar al servicio para actualizar una vista de consulta
    return services.update(view_id, body, current_user)

@bp.route('/<view_id>', methods=['DELETE'])
@jwt_required()
def delete_view(view_id):
    """
    Eliminar una vista de consulta
    ---
    tags:
        - Vistas
    parameters:
        - in: path
          name: view_id
          type: string
          required: true
    responses:
        200:
            description: Vista de consulta eliminada exitosamente
        401:
            description: No tienes permisos para realizar esta acción
        500:
            description: Error al eliminar la vista de consulta
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para eliminar una vista de consulta
    return services.delete(view_id, current_user)

# Nuevo POST endpoint para crear una nueva vista de consulta
@bp.route('', methods=['POST'])
@jwt_required()
def new_view():
    """
    Crear una nueva vista de consulta
    ---
    tags:
        - Vistas
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
                metadata:
                    type: array
                    items:
                        type: object
                icon:
                    type: string
                hierarchical:
                    type: boolean
                parentType:
                    type: string
    responses:
        200:
            description: Vista de consulta creada exitosamente
        401:
            description: No tienes permisos para realizar esta acción
        500:
            description: Error al crear la vista de consulta
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.json
    # Llamar al servicio para crear la vista de consulta
    return services.create(body, current_user)
