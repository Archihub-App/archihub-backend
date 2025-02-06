from app.api.search import bp
from app.api.search import public_services
from flask import request, jsonify

@bp.route('/public', methods=['POST'])
def get_all_public():
    """
    Obtener todos los resources dado un body de filtros
    ---
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
    body = request.json
    resp = public_services.get_resources_by_filters(body)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp