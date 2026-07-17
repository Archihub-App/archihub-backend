from app.api.types import bp
from app.api.types import services
from flask import request

@bp.route('/info', methods=['POST'])
def get_types_info():
    """
    Obtener estadísticas públicas de un tipo de contenido y su jerarquía (padres/hijos)
    ---
    tags:
        - Tipos de contenido
    description: No requiere autenticación. Devuelve, para el tipo indicado, sus tipos relacionados (padres si es hijo, o padres e hijos si es un tipo padre) con conteo de recursos publicados y porcentaje, más el total y desglose de archivos (records) asociados.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                post_type:
                    type: string
                    description: slug del tipo de contenido
            required:
                - post_type
    responses:
        200:
            description: Información de los tipos de contenido
        500:
            description: Error al obtener la información de los tipos de contenido (incluye post_type inexistente o ausente)
    """
    body = request.get_json()
    resp = services.get_types_info(body)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp