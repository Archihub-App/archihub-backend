from app.api.snaps import bp
from flask import request
from app.api.snaps import public_services

@bp.route('/public/<id>', methods=['GET'])
def get_public_snap(id):
    """
    Obtener un recorte público por su id, sin autenticación (usa el flujo público de records para validar acceso)
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
            description: >
                Para type=document/image/video: una imagen JPEG recortada (image/jpeg). Para type=audio: un
                stream del fragmento de audio. Para cualquier otro type: el documento JSON del recorte.
        404:
            description: Recorte no encontrado
        500:
            description: Error obteniendo el recorte, o el record asociado no es accesible públicamente
    """
    resp = public_services.get_by_id(id)
    return resp