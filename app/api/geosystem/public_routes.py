from app.api.geosystem import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.geosystem import services
from app.api.users import services as user_services
from flask import request

@bp.route('/level', methods=['POST'])
def get_level():
    """
    Obtener un nivel de consulta
    ---
    tags:
        - Niveles
    responses:
        200:
            description: Retorna el nivel de consulta
        500:
            description: Error al obtener el nivel de consulta
    """
    body = request.json
    # Llamar al servicio para obtener un nivel de consulta
    resp = services.get_level(body)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp