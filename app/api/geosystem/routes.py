from app.api.geosystem import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.geosystem import services
from flask import request

@bp.route('/polygon', methods=['POST'])
def get_polygon():
    """
    Obtener el/los polígono(s) geográfico(s) de una o varias formas administrativas
    ---
    tags:
        - Niveles
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                ident:
                    type: string
                    description: Identificador de la forma. Si se omite, se retorna un listado de formas que cumplan el resto de filtros.
                parent:
                    type: string
                    description: Identificador de la forma padre (filtro opcional).
                level:
                    type: integer
                    description: Nivel administrativo (properties.admin_level) a consultar.
                retention:
                    type: number
                    default: 0.1
                    description: Porcentaje de retención de puntos usado al simplificar la geometría.
                type:
                    type: string
                    description: Tipo de forma (properties.shape_type). El valor especial "administrative" ignora ident/level y fuerza level=1 usando ident como parent.
    responses:
        200:
            description: >
                Si se envía "ident", retorna un único feature GeoJSON (geometry + properties.name/ident/type).
                Si no se envía, retorna un arreglo de features GeoJSON que cumplen los filtros (puede ser vacío).
        404:
            description: No se encontró una forma con el "ident" solicitado (solo aplica cuando se envía "ident")
        500:
            description: Error al obtener el polígono
    """
    data = request.get_json()
    ident = data.get('ident')
    parent = data.get('parent')
    level = data.get('level')
    retention = data.get('retention', 0.1)
    type = data.get('type', None)
    
    resp = services.get_shape_by_ident(ident, parent, level, type, retention)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp