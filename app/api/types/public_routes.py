from app.api.types import bp
from app.api.types import services
from flask import request

@bp.route('/info', methods=['POST'])
def get_types_info():
    """
    Obtener información de los tipos de contenido
    ---
    tags:
        - Tipos de contenido
    responses:
        200:
            description: Información de los tipos de contenido
        500:
            description: Error al obtener la información de los tipos de contenido
    """
    body = request.get_json()
    resp = services.get_types_info(body)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp