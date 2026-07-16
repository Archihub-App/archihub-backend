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
    Buscar recursos autenticados por filtros (Elasticsearch o vector DB, según capacidades activas)
    ---
    security:
        - JWT: []
    tags:
        - Recursos
    description: >
        Solo funciona si el blueprint "search" está registrado (requiere que el sistema tenga
        activo `index_management.index_activation` y/o `.vector_activation`; si no, la ruta
        no existe -> 404 genérico de Flask). El body se delega tal cual en
        app.api.search.utils.elasticUtils.get_resources_by_filters (searchSource='index',
        por defecto) o vectorUtils (searchSource='vector'). `post_type` es obligatorio; el
        resto de campos son opcionales y sus valores por defecto se aplican en el motor de
        búsqueda subyacente.
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
                    description: "'index' (Elasticsearch, por defecto) o 'vector'"
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
            description: Resources obtenidos exitosamente
        401:
            description: Token JWT ausente o inválido (respuesta estándar de flask_jwt_extended)
        500:
            description: >
                Error al obtener los resources (p.ej. falta "post_type" en el body, no hay
                motor de búsqueda activo). Nota: un rol insuficiente para ver alguno de los
                post_type solicitados también cae aquí con 500 ("You don't have the required
                authorization"), no con 401, porque la excepción se captura genéricamente.
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Obtener el body del request
    body = request.json
    
    # Llamar al servicio para obtener los resources
    return services.get_resources_by_filters(body, current_user)