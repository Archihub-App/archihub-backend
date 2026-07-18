from app.api.snaps import bp
from flask import request
from app.api.snaps import public_services

@bp.route('/public/<id>', methods=['GET'])
def get_public_snap(id):
    """
    Get a public snap by its id, without authentication (uses the public records flow to validate access)
    ---
    tags:
      - Snaps
    parameters:
      - in: path
        name: id
        schema:
          type: string
        required: true
        description: Id of the snap
    responses:
        200:
            description: >
                For type=document/image/video: a cropped JPEG image (image/jpeg). For type=audio: a
                stream of the audio fragment. For any other type: the snap's JSON document.
        404:
            description: Snap not found
        500:
            description: Error retrieving the snap, or the associated record is not publicly accessible
    """
    resp = public_services.get_by_id(id)
    return resp