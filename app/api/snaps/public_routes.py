from app.api.snaps import bp
from flask import request
from app.api.snaps import public_services

@bp.route('/public/<id>', methods=['GET'])
def get_public_snap(id):
    """
    Obtener un recorte por su id
    ---
    tags:
      - Recortes
    parameters:
      - in: path
        name: id
        schema:
          type: string
        required: true
        description: Id del recorte
    responses:
        200:
            description: Recorte encontrado
        401:
            description: No tienes permisos para ver este recorte
        404:
            description: Recorte no encontrado
        500:
            description: Error obteniendo el recorte
    """
    resp = public_services.get_by_id(id)
    return resp