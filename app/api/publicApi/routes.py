from app.api.publicApi import bp
from flask import jsonify
from flask import request
from app.utils.FernetAuth import publicFernetAuthenticate as fernetAuthenticate

# En este archivo se registran las rutas de la API para los logs

# Nuevo POST endpoint para obtener los logs de acuerdo a un filtro
@bp.route('', methods=['GET'])
@fernetAuthenticate
def filter(username, isAdmin):
    """
    Obtener los logs de acuerdo a un filtro
    ---
    security:
        - JWT: []
    tags:
        - API pública
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
    
    return jsonify({'msg': 'Logs obtenidos exitosamente'}), 200