from app.api.geosystem import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.geosystem import services
from flask import request

@bp.route('/polygon', methods=['POST'])
def get_polygon():
    """
    Get the geographic polygon(s) for one or more administrative shapes
    ---
    tags:
        - Levels
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                ident:
                    type: string
                    description: Identifier of the shape. If omitted, a list of shapes matching the remaining filters is returned.
                parent:
                    type: string
                    description: Identifier of the parent shape (optional filter).
                level:
                    type: integer
                    description: Administrative level (properties.admin_level) to query.
                retention:
                    type: number
                    default: 0.1
                    description: Point retention percentage used when simplifying the geometry.
                type:
                    type: string
                    description: Shape type (properties.shape_type). The special value "administrative" ignores ident/level and forces level=1 using ident as parent.
    responses:
        200:
            description: >
                If "ident" is sent, returns a single GeoJSON feature (geometry + properties.name/ident/type).
                If not sent, returns an array of GeoJSON features matching the filters (can be empty).
        404:
            description: No shape was found with the requested "ident" (only applies when "ident" is sent)
        500:
            description: Error retrieving the polygon
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