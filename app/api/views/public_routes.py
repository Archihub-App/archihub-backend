from app.api.views import bp
from flask import jsonify
from flask import request
from app.api.views import services


@bp.route('/info/<view_slug>', methods=['GET'])
def get_view_info(view_slug):
    """
    Obtener información de una vista de consulta
    ---
    tags:
        - Vistas
    responses:
        200:
            description: Información de la vista de consulta
        500:
            description: Error al obtener la información de la vista de consulta
    """
    # Llamar al servicio para obtener la información de una vista de consulta
    resp = services.get_view_info(view_slug)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp