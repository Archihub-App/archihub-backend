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
    Search authenticated resources by filters (Elasticsearch or vector DB, depending on active capabilities)
    ---
    security:
        - JWT: []
    tags:
        - Resources
    description: >
        Only works if the "search" blueprint is registered (requires the system to have
        `index_management.index_activation` and/or `.vector_activation` active; otherwise the
        route doesn't exist -> generic Flask 404). The body is passed as-is to
        app.api.search.utils.elasticUtils.get_resources_by_filters (searchSource='index',
        default) or vectorUtils (searchSource='vector'). `post_type` is required; the
        remaining fields are optional and their defaults are applied in the underlying
        search engine.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                post_type:
                    type: array
                    items:
                        type: string
                keyword:
                    type: string
                searchSource:
                    type: string
                    description: "'index' (Elasticsearch, default) or 'vector'"
                sortBy:
                    type: string
                    default: createdAt
                sortOrder:
                    type: string
                    default: asc
                activeColumns:
                    type: array
                    items:
                        type: object
                viewType:
                    type: string
                    default: list
                size:
                    type: integer
                    default: 20
                operator:
                    type: string
                    default: AND
                record_types:
                    type: array
                    items:
                        type: string
                        enum: [image, document, video, audio]
                parents:
                    type: object
            required:
                - post_type
    responses:
        200:
            description: Resources retrieved successfully
        401:
            description: Missing or invalid JWT token (standard flask_jwt_extended response)
        500:
            description: >
                Error retrieving resources (e.g. missing "post_type" in the body, no active
                search engine). Note: insufficient role to view one of the requested
                post_type values also lands here with 500 ("You don't have the required
                authorization"), not 401, because the exception is caught generically.
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Obtener el body del request
    body = request.json
    
    # Llamar al servicio para obtener los resources
    return services.get_resources_by_filters(body, current_user)