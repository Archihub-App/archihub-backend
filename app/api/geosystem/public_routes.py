from app.api.geosystem import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.geosystem import services
from app.api.users import services as user_services
from flask import request

@bp.route('/level', methods=['POST'])
def get_level():
    """
    Obtener las formas geográficas de un nivel administrativo, opcionalmente filtradas por padre o por un área geográfica (bounds)
    ---
    tags:
        - Niveles
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                level:
                    type: integer
                    default: 0
                    description: Nivel administrativo (properties.admin_level) a consultar.
                parent:
                    type: string
                    description: Identificador de la forma padre (filtro opcional).
                area_threshold:
                    type: number
                    description: Área mínima (en las unidades de la geometría) que debe tener una forma para incluirse. Se ignora si level=0 (se usa 4.0) o si se envía "bounds" con área intermedia/pequeña (se recalcula automáticamente).
                bounds:
                    type: object
                    description: Rectángulo geográfico opcional usado para filtrar por intersección espacial y para ajustar automáticamente el nivel/umbral de simplificación según su área.
                    properties:
                        minLng:
                            type: number
                        minLat:
                            type: number
                        maxLng:
                            type: number
                        maxLat:
                            type: number
    responses:
        200:
            description: Arreglo de formas (geometry + properties.name/ident + centroid), simplificadas y filtradas por área mínima. Puede ser vacío.
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