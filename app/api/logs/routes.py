from app.api.logs import bp
from flask import jsonify
from flask import request
from . import services
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity

# En este archivo se registran las rutas de la API para los logs

# Nuevo POST endpoint para obtener los logs de acuerdo a un filtro
@bp.route('/filter', methods=['POST'])
@jwt_required()
def filter():
    """
    Obtener los logs de acuerdo a un filtro
    ---
    security:
        - JWT: []
    tags:
        - Logs
    parameters:
        - in: body
            name: body
            schema:
                type: object
                properties:
                    username:
                        type: string
                    action:
                        type: string
                    from_date:
                        type: string
                    to_date:
                        type: string
    responses:
        200:
            description: Logs obtenidos exitosamente
        400:
            description: No se encontraron logs
    """

    return 'test'