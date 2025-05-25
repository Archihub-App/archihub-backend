from app.api.search import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.search import services
from flask import request, jsonify
from app.api.users import services as user_services
from app.api.resources.services import cache_type_roles
import json
from flask_babel import _

# En este archivo se registran las rutas de la API para la búsqueda

# Nuevo endpoint para obtener todos los resources dado un body de filtros
@bp.route('', methods=['POST'])
@jwt_required()
def get_all():
    """
    Obtener todos los resources dado un body de filtros
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
            description: Resources obtenidos exitosamente
        401:
            description: No tiene permisos para obtener los resources
        500:
            description: Error al obtener los resources
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Obtener el body del request
    body = request.json
    
    # Llamar al servicio para obtener los resources
    return services.get_resources_by_filters(body, current_user)