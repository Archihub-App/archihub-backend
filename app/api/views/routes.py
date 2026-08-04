from app.api.views import bp
from flask_jwt_extended import jwt_required, get_jwt_identity
from flask import jsonify, request
from app.api.views import services
from app.api.users import services as user_services
from flask_babel import _
import json

@bp.route('/<view_id>', methods=['GET'])
@jwt_required()
def get_view(view_id):
    """
    Get a query view by its id (includes its base64 thumbnail if it has one)
    ---
    security:
        - JWT: []
    tags:
        - Views
    description: Requires the "admin" or "editor" role.
    parameters:
        - in: path
          name: view_id
          type: string
          required: true
          description: MongoDB ObjectId of the view
    responses:
        200:
            description: Returns the query view
        401:
            description: You don't have the required admin/editor role
        404:
            description: The view does not exist
        500:
            description: Unhandled internal error (e.g. view_id with an invalid ObjectId format)
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para obtener una vista de consulta
    resp = services.get(view_id, current_user)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/<view_id>', methods=['PUT'])
@jwt_required()
def update_view(view_id):
    """
    Update a query view (name, description, visible types, thumbnail, etc.)
    ---
    security:
        - JWT: []
    tags:
        - Views
    consumes:
        - multipart/form-data
    description: >
        Requires the "admin" or "editor" role. The body is `multipart/form-data`, not JSON: a
        `data` field with the serialized JSON of the fields to update (see ViewUpdate:
        name, description, parent, root, visible, defaultView, slug — all optional) and,
        optionally, a SINGLE image file under `files` that replaces the thumbnail
        (the view's previous associated file is deleted).
    parameters:
        - in: path
          name: view_id
          type: string
          required: true
          description: MongoDB ObjectId of the view
        - in: formData
          name: data
          type: string
          required: true
          description: Serialized JSON with the fields to update
        - in: formData
          name: files
          type: file
          required: false
          description: At most one image file (jpg/jpeg/png/gif/tif/tiff/heic/bmp/webp)
    responses:
        200:
            description: Query view updated successfully
        400:
            description: More than one file was sent, or the file is not a supported image
        401:
            description: You don't have the required admin/editor role
        404:
            description: The view does not exist
        500:
            description: Error updating the query view (e.g. missing "data" in the form, or image processing failed)
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.form.to_dict()
    data = body.get('data')
    data = json.loads(data)
    
    files = request.files.getlist('files')
    
    # Llamar al servicio para actualizar una vista de consulta
    return services.update(view_id, data, current_user, files)

@bp.route('/<view_id>', methods=['DELETE'])
@jwt_required()
def delete_view(view_id):
    """
    Delete a query view (and its associated thumbnail, if it has one)
    ---
    security:
        - JWT: []
    tags:
        - Views
    description: Requires the "admin" or "editor" role.
    parameters:
        - in: path
          name: view_id
          type: string
          required: true
          description: MongoDB ObjectId of the view
    responses:
        200:
            description: Query view deleted successfully (also returned if the id didn't exist — there's no explicit 404)
        401:
            description: You don't have the required admin/editor role
        500:
            description: Error deleting the query view
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para eliminar una vista de consulta
    return services.delete(view_id, current_user)

# Nuevo POST endpoint para crear una nueva vista de consulta
@bp.route('', methods=['POST'])
@jwt_required()
def new_view():
    """
    Create a new query view
    ---
    security:
        - JWT: []
    tags:
        - Views
    consumes:
        - multipart/form-data
    description: >
        Requires the "admin" or "editor" role. The body is `multipart/form-data`, not JSON: a
        `data` field with the serialized JSON of the view (see the View model: name, slug,
        description, parent, root, visible are required; defaultView defaults to
        "list") and, optionally, a SINGLE image file under `files` as the thumbnail.
    parameters:
        - in: formData
          name: data
          type: string
          required: true
          description: 'Serialized JSON, e.g. {"name": "...", "slug": "...", "description": "...", "parent": "", "root": "...", "visible": ["..."]}'
        - in: formData
          name: files
          type: file
          required: false
          description: At most one image file (jpg/jpeg/png/gif/tif/tiff/heic/bmp/webp)
    responses:
        201:
            description: Query view created successfully
        400:
            description: More than one file was sent, or the file is not a supported image
        401:
            description: You don't have the required admin/editor role
        500:
            description: Error creating the query view (e.g. a required field is missing in "data", or image processing failed)
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.form.to_dict()
    data = body.get('data')
    data = json.loads(data)
    
    files = request.files.getlist('files')
    # Llamar al servicio para crear la vista de consulta
    return services.create(data, current_user, files)
