from app.api.geosystem import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.geosystem import services
from app.api.users import services as user_services
from flask import request

@bp.route('/level', methods=['POST'])
def get_level():
    """
    Get the geographic shapes for an administrative level, optionally filtered by parent or by a geographic area (bounds)
    ---
    tags:
        - Levels
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                level:
                    type: integer
                    default: 0
                    description: Administrative level (properties.admin_level) to query.
                parent:
                    type: string
                    description: Identifier of the parent shape (optional filter).
                area_threshold:
                    type: number
                    description: Minimum area (in the geometry's units) a shape must have to be included. Ignored if level=0 (4.0 is used instead) or if "bounds" is sent with an intermediate/small area (recalculated automatically).
                bounds:
                    type: object
                    description: Optional geographic rectangle used to filter by spatial intersection and to automatically adjust the level/simplification threshold based on its area.
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
            description: Array of shapes (geometry + properties.name/ident + centroid), simplified and filtered by minimum area. May be empty.
        500:
            description: Error retrieving the query level
    """
    body = request.json
    # Llamar al servicio para obtener un nivel de consulta
    resp = services.get_level(body)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp