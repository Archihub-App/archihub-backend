from app.api.adminApi import bp
from flask import jsonify
from flask import request
from app.utils.FernetAuth import fernetAuthenticate

# Nuevo POST endpoint para obtener los logs de acuerdo a un filtro
@bp.route('', methods=['GET'])
@fernetAuthenticate
def filter():
    """
    Endpoint de prueba
    ---
    security:
        - JWT: []
    tags:
        - API de administración
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
    responses:
        200:
            description: Logs obtenidos exitosamente
        400:
            description: No se encontraron logs
    """
    
    return 'ok'