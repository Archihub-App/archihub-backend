from app.api.snaps import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from flask import request
from app.api.snaps import services

@bp.route('', methods=['POST'])
@jwt_required()
def create_snap():
    """
    Create a new snap (clip) of a record, associated with the authenticated user
    ---
    security:
      - JWT: []
    tags:
      - Snaps
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            record_id:
              type: string
              description: Id of the record the snap is being created from.
            type:
              type: string
              description: "Snap type: document, image, video, or audio."
            data:
              type: object
              description: >
                Snap-specific data depending on the type (e.g. bbox {x,y,width,height} and page for
                document/image, begin/end in milliseconds for audio/video).
          required:
            - record_id
            - type
            - data
    responses:
        201:
            description: Snap created successfully
        404:
            description: The referenced record (record_id) does not exist
        500:
            description: Error creating the snap (includes the case where a required body field is missing)
    """
    user = get_jwt_identity()
    body = request.json

    return services.create(user, body)

@bp.route('/<id>', methods=['DELETE'])
@jwt_required()
def delete_snap(id):
    """
    Delete a snap by its id (only the snap's owning user can delete it)
    ---
    security:
      - JWT: []
    tags:
      - Snaps
    parameters:
      - in: path
        name: id
        schema:
          type: string
        required: true
        description: Snap id
    responses:
        204:
            description: Snap deleted successfully
        401:
            description: The snap exists but belongs to another user
        404:
            description: Snap not found
        500:
            description: Error deleting the snap
    """
    user = get_jwt_identity()

    return services.delete_by_id(id, user)

@bp.route('/<id>', methods=['GET'])
@jwt_required()
def get_snap(id):
    """
    Get a snap by its id (only the snap's owning user can view it)
    ---
    security:
      - JWT: []
    tags:
      - Snaps
    parameters:
      - in: path
        name: id
        schema:
          type: string
        required: true
        description: Snap id
    responses:
        200:
            description: >
                For type=document/image/video: a cropped JPEG image (image/jpeg) generated from the
                bbox stored in the snap. For type=audio: a stream of the audio fragment. For
                any other type: the snap's JSON document (record_id, type, data).
        401:
            description: The snap exists but belongs to another user
        404:
            description: Snap not found
        500:
            description: Error retrieving the snap (includes failures reading/processing the associated file)
    """
    user = get_jwt_identity()

    resp = services.get_by_id(id, user)
    return resp