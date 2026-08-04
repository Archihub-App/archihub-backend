from app.api.types import bp
from app.api.types import services
from flask import request

@bp.route('/info', methods=['POST'])
def get_types_info():
    """
    Get public statistics for a content type and its hierarchy (parents/children)
    ---
    tags:
        - Content Types
    description: Does not require authentication. For the given type, returns its related types (parents if it's a child, or parents and children if it's a parent type) with a count and percentage of published resources, plus the total and breakdown of associated files (records).
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                post_type:
                    type: string
                    description: slug of the content type
            required:
                - post_type
    responses:
        200:
            description: Content type information
        500:
            description: Error retrieving content type information (includes a nonexistent or missing post_type)
    """
    body = request.get_json()
    resp = services.get_types_info(body)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp