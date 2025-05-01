from app.api.logs import bp
from flask import jsonify
from flask import request
from . import services
from app.api.users import services as user_services
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from flask_babel import _
from app.utils.FernetAuth import fernetAuthenticate
# En este archivo se registran las rutas de la API para los logs

# Nuevo POST endpoint para obtener los logs de acuerdo a un filtro
@bp.route('', methods=['POST'])
@jwt_required()
def filter():
    """
    Obtener los logs de acuerdo a un filtro
    ---
    security:
        - JWT: []
    tags:
        - Logs del sistema
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
              username:
                type: string
              action:
                type: string
    responses:
        200:
            description: Logs obtenidos exitosamente
        400:
            description: No se encontraron logs
        403:
            description: No tienes permisos para realizar esta acción
        500:
            description: Error obteniendo logs
    """
    # Obtener el body del request
    body = request.json
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para obtener los logs de acuerdo a un filtro
    return services.filter(body)

# Nuevo GET endpoint para obtener todos los logs de un recurso
@bp.route('/resource/<resource_id>', methods=['POST'])
@jwt_required()
def get_logs(resource_id):
    """
    Obtener todos los logs de un recurso
    ---
    tags:
        - Logs del sistema
    parameters:
        - in: path
          name: resource_id
          required: true
          type: string
          description: ID del recurso
        - in: body
          name: body
          schema:
                type: object
                properties:
                    page:
                        type: number
    responses:
        200:
            description: Logs obtenidos exitosamente
        404:
            description: No se encontraron logs
        500:
            description: Error obteniendo logs
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    
    # Obtener el body del request
    body = request.json

    # Llamar al servicio para obtener los logs de un recurso
    return services.get_logs(body, resource_id)
