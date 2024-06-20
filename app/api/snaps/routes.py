from app.api.snaps import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from flask import request
from app.api.snaps import services

@bp.route('', methods=['POST'])
@jwt_required()
def create_snap():
    """
    Crear un nuevo recorte
    ---
    tags:
      - snaps
    responses:
        200:
            description: Recorte creado
        400:
            description: Error en la petición
        401:
            description: Token inválido
    """
    user = get_jwt_identity()
    body = request.json

    return services.create(user, body)