from app.api.types import bp
from app.api.types import services

@bp.route('/info/<post_type>', methods=['GET'])
def get_types_info(post_type):
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
    resp = services.get_types_info(post_type)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp