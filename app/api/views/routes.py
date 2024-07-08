from app.api.views import bp
from flask_jwt_extended import jwt_required, get_jwt_identity
from flask import jsonify, request
from app.api.views import services
from app.api.users import services as user_services

# Nuevo POST endpoint para crear una nueva vista de consulta
@bp.route('/create', methods=['POST'])
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
        500:
            description: Error al crear la vista de consulta
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Obtener el body del request
    body = request.json
    # Llamar al servicio para crear la vista de consulta
    return services.create(body, current_user)
