from app.api.forms import bp
from flask import jsonify, request
from flask_jwt_extended import jwt_required
from app.api.forms import services
from app.api.users import services as user_services
from flask_jwt_extended import get_jwt_identity
from flask_babel import _

# En este archivo se registran las rutas de la API para los estándares de metadatos

# Nuevo endpoint para obtener todos los estándares de metadatos
@bp.route('', methods=['GET'])
@jwt_required()
def get_all():
    """
    Get all metadata standards from the database
    ---
    security:
        - JWT: []
    tags:
        - Metadata Standards
    description: Requires the admin role. Returns only the name, description, and slug fields of each form (not the full fields list).
    responses:
        200:
            description: List of metadata standards retrieved successfully (name, description, slug only)
        401:
            description: You don't have permission to perform this action (not an admin)
        500:
            description: Error retrieving the metadata standards
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para obtener todos los estándares de metadatos
    resp = services.get_all()
    if isinstance(resp, list):
        return tuple(resp)
    return resp

# Nuevo endpoint para crear un estándar de metadatos
@bp.route('', methods=['POST'])
@jwt_required()
def create():
    """
    Create a new metadata standard (form) with the request body
    ---
    security:
        - JWT: []
    tags:
        - Metadata Standards
    description: Requires the admin role. If slug is not sent (or is empty), it's auto-generated from name; if the slug already exists, an incremental numeric suffix is appended. Exactly one of the fields entries must have destiny equal to metadata.firstLevel.title and be of type text, or creation fails. All validation exceptions are returned as 500, not 400.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
                description:
                    type: string
                slug:
                    type: string
                    description: Optional; auto-generated from name if omitted or empty.
                fields:
                    type: array
                    items:
                        type: object
                        description: Each field requires at least label; if it has destiny, it must start with "metadata" (except type separator/file), and cannot be equal to "ident".
            required:
                - name
                - description
                - fields
    responses:
        201:
            description: Metadata standard created successfully
        401:
            description: You don't have permission to perform this action (not an admin)
        500:
            description: Error creating the metadata standard (includes fields validation errors, e.g. missing the field with destiny metadata.firstLevel.title)
    """
    # Obtener el body de la request
    body = request.json
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    
    return services.create(body, current_user)
    

# Nuevo endpoint para devolver un estándar por su slug
@bp.route('/<slug>', methods=['POST'])
@jwt_required()
def get_by_slug(slug):
    """
    Get a form (metadata standard) by its slug
    ---
    security:
        - JWT: []
    tags:
        - Metadata Standards
    description: Requires the admin role. Note that this route accepts the POST method, not GET. The returned form includes an accessRights field automatically injected at the start of fields.
    parameters:
        - in: path
          name: slug
          type: string
          required: true
    responses:
        200:
            description: Form retrieved successfully
        401:
            description: You don't have permission to perform this action (not an admin)
        404:
            description: Form not found
        500:
            description: Error retrieving the form
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Llamar al servicio para obtener el estándar por su slug
    resp, status = services.get_by_slug(slug)

    # Si el estándar no existe, retornar error
    if status == 404:
        return jsonify(resp), 404
    # Retornar el estándar
    return jsonify(resp), 200

# Nuevo endpoint para actualizar un estándar por su slug
@bp.route('/<slug>', methods=['PUT'])
@jwt_required()
def update_by_slug(slug):
    """
    Update a form (metadata standard) by its slug
    ---
    security:
        - JWT: []
    tags:
        - Metadata Standards
    description: Requires the admin role. The combined metadata schema of all forms is validated and recalculated before saving; fields validation applies the same as on creation.
    parameters:
        - in: path
          name: slug
          schema:
            type: string
          required: true
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
                description:
                    type: string
                fields:
                    type: array
                    items:
                        type: object
            required:
                - fields
    responses:
        200:
            description: Metadata standard updated successfully
        401:
            description: You don't have permission to perform this action (not an admin)
        404:
            description: Form not found
        500:
            description: Error updating the metadata standard (includes fields validation errors)
    """
    # Obtener el body de la request
    body = request.json
    
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para actualizar el estándar por su slug
    return services.update_by_slug(slug, body, current_user)

# Nuevo endpoint para eliminar un estándar por su slug
@bp.route('/<slug>', methods=['DELETE'])
@jwt_required()
def delete_by_slug(slug):
    """
    Delete a form (metadata standard) by its slug
    ---
    security:
        - JWT: []
    tags:
        - Metadata Standards
    description: Requires the admin role. Rejected if any content type (post_type) uses this form as its metadata.
    parameters:
        - in: path
          name: slug
          schema:
            type: string
          required: true
    responses:
        204:
            description: Form deleted successfully (no content in the response)
        400:
            description: The form is being used by a content type
        401:
            description: You don't have permission to perform this action (not an admin)
        404:
            description: Form not found
        500:
            description: Error deleting the form
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para eliminar el estándar por su slug
    return services.delete_by_slug(slug, current_user)

# Nuevo endpoint para duplicar un estándar por su slug
@bp.route('/duplicate/<slug>', methods=['POST'])
@jwt_required()
def duplicate_by_slug(slug):
    """
    Duplicate a form (metadata standard) by its slug
    ---
    security:
        - JWT: []
    tags:
        - Metadata Standards
    description: Requires the admin role. Creates a copy named "<name> (copy)"; the new slug is generated the same way as on creation (from the new name, with a numeric suffix on collision).
    parameters:
        - in: path
          name: slug
          schema:
            type: string
          required: true
    responses:
        201:
            description: Form duplicated successfully
        401:
            description: You don't have permission to perform this action (not an admin)
        404:
            description: Original form not found
        500:
            description: Error duplicating the form
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para duplicar el estándar por su slug
    return services.duplicate_by_slug(slug, current_user)

@bp.route('/fields', methods=['GET'])
@jwt_required()
def get_all_fields():
    """
    Get all field types available for forms
    ---
    security:
        - JWT: []
    tags:
        - Field Types
    description: Requires the admin role. Fixed list of field types (text, text-area, number, simple-date, select, select-multiple2, checkbox, file, repeater, separator, author, location, userslit), extensible by plugins via the get_fields_types hook.
    responses:
        200:
            description: List of field types retrieved successfully
        401:
            description: You don't have permission to perform this action (not an admin)
        500:
            description: Error retrieving the field types
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para obtener todos los tipos de campos
    resp = services.get_all_fields_types()
    if isinstance(resp, list):
        return resp[0], 200
    return resp