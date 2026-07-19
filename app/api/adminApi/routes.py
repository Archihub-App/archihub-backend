from app.api.adminApi import bp
from flask import jsonify
from flask import request
from app.api.adminApi import services
from app.utils.FernetAuth import fernetAuthenticate
import json
from flask_babel import _

@bp.route('/get_system_info', methods=['GET'])
@fernetAuthenticate
def get_info(username, isAdmin):
    """
    Get general system information (content types, active capabilities, and metrics)
    ---
    security:
        - JWT: []
    tags:
        - Admin API
    description: >
        Requires an admin token issued by the API's token flow
        (an encrypted Fernet token sent in the `Authorization: Bearer <token>` header,
        distinct from the normal JWT from `/auth/login`). The user must have the `admin` role.
    responses:
        200:
            description: System information retrieved successfully (post_types, capabilities, metrics)
        401:
            description: Token not provided, invalid, expired, or the user does not have the admin role
        500:
            description: Error retrieving the system information
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    return services.get_system_info(username)

# New POST endpoint for creating new resources
@bp.route('/create', methods=['POST'])
@fernetAuthenticate
def new_resource(username, isAdmin):
    """
    Create a new resource (delegates to app.api.resources.services.create after filling in default fields)
    ---
    security:
        - JWT: []
    tags:
        - Admin API
    consumes:
        - multipart/form-data
    description: >
        Requires an admin token (see `/get_system_info`). The body is NOT JSON: it's
        `multipart/form-data` with a `data` field containing the serialized JSON of the resource
        and, optionally, one or more files under the `files` field. If `post_type`, `status`,
        `parent`, `parents`, `filesIds`, or `updateCache` are not present in `data`, they are
        filled in with default values before forwarding the request to the resources service.
    parameters:
        - in: formData
          name: data
          type: string
          required: true
          description: 'Serialized JSON, e.g. {"metadata": {...}, "post_type": "...", "status": "published"}'
        - in: formData
          name: files
          type: file
          required: false
          description: One or more files to attach to the resource (can be repeated)
    responses:
        201:
            description: Resource created successfully
        400:
            description: The resource is missing metadata required by its content type
        401:
            description: Token not provided/invalid or the user does not have the admin role
        500:
            description: Error creating the resource (e.g. missing "data" field in the form)
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Get the request body
    body = request.form.to_dict()
    files = request.files.getlist('files')
    data = json.loads(body['data'])
    # Call the service to create the resource
    return services.create(data, username, files)

# New POST endpoint for updating a resource
@bp.route('/update', methods=['POST'])
@fernetAuthenticate
def update_resource(username, isAdmin):
    """
    Update an existing resource (delegates to app.api.resources.services.update_by_id)
    ---
    security:
        - JWT: []
    tags:
        - Admin API
    consumes:
        - multipart/form-data
    description: >
        Requires an admin token. Same as `/create`, the body is `multipart/form-data`,
        not JSON: an `id` field with the resource id, a `data` field with the serialized JSON of
        the changes, and optionally new files under `files`. `deletedFiles` is filled in with
        `[]` in `data` if not provided.
    parameters:
        - in: formData
          name: id
          type: string
          required: true
          description: Id of the resource to update (KeyError/500 if missing)
        - in: formData
          name: data
          type: string
          required: true
          description: Serialized JSON with the fields to update
        - in: formData
          name: files
          type: file
          required: false
          description: New files to attach (can be repeated)
    responses:
        200:
            description: Resource updated successfully
        401:
            description: Token not provided/invalid or the user does not have the admin role
        500:
            description: Error updating the resource (e.g. missing "id" or "data" in the form)
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Get the request body
    body = request.form.to_dict()
    files = request.files.getlist('files')
    data = json.loads(body['data'])

    # Call the service to update the resource
    return services.update(body['id'], data, username, files)

# New POST endpoint for getting a resource's id by its name
@bp.route('/get_id', methods=['POST'])
@fernetAuthenticate
def get_resource_id(username, isAdmin):
    """
    Get the id (and basic fields) of a single published resource matching a filter
    ---
    security:
        - JWT: []
    tags:
        - Admin API
    description: >
        The body is used AS-IS as a MongoDB filter on the `resources` collection
        (`status: "published"` is automatically added to it), so it can include
        any indexed field of the resource (e.g. a metadata path), not just `name`.
        Returns the first matching resource.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            description: 'Arbitrary MongoDB filter, e.g. {"metadata.firstLevel.title": "..."}'
    responses:
        200:
            description: Resource found (id, post_type, metadata, filesObj, parent, parents)
        401:
            description: Token not provided/invalid or the user does not have the admin role
        404:
            description: No published resource matching the filter was found
        500:
            description: Error retrieving the resource id
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Get the request body
    body = request.json

    # Call the service to get the resource id
    return services.get_id(body, username)

# New POST endpoint for getting a resource's id by its name
@bp.route('/get_opts_id', methods=['POST'])
@fernetAuthenticate
def get_opts_id(username, isAdmin):
    """
    Get the id of a list option (the "options" collection) by its exact term
    ---
    security:
        - JWT: []
    tags:
        - Admin API
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                term:
                    type: string
            required:
                - term
    responses:
        200:
            description: Option id retrieved successfully
        401:
            description: Token not provided/invalid or the user does not have the admin role
        404:
            description: No option exists with that term
        500:
            description: Error retrieving the option id (e.g. missing "term" in the body)
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Get the request body
    body = request.json

    # Call the service to get the resource id
    return services.get_opts_id(body, username)

@bp.route('/create_type', methods=['POST'])
@fernetAuthenticate
def create_type(username, isAdmin):
    """
    Create a new content type (delegates to app.api.types.services.create)
    ---
    security:
        - JWT: []
    tags:
        - Admin API
    description: Requires an admin token. The body is forwarded as-is to app.api.types.services.create.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
                slug:
                    type: string
                description:
                    type: string
                metadata:
                    type: array
                    items:
                        type: object
                icon:
                    type: string
            required:
                - name
                - slug
    responses:
        201:
            description: Content type created successfully
        400:
            description: The content type's name or slug is empty
        401:
            description: You do not have permission to create a content type
        500:
            description: Error creating the content type
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    # Get the request body
    body = request.json

    # Call the service to create the content type
    return services.create_type(body, username)

@bp.route('/update_type', methods=['POST'])
@fernetAuthenticate
def update_type(username, isAdmin):
    """
    Update a content type (delegates to app.api.types.services.update_by_slug)
    ---
    security:
        - JWT: []
    tags:
        - Admin API
    description: >
        Requires an admin token. `slug` identifies the type to update and is
        required (read directly from `body['slug']`; its absence produces a 500 error,
        not a 400). There is no `id` field for this endpoint.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                slug:
                    type: string
                name:
                    type: string
                description:
                    type: string
                metadata:
                    type: array
                    items:
                        type: object
                icon:
                    type: string
            required:
                - slug
    responses:
        200:
            description: Content type updated successfully
        401:
            description: Token not provided/invalid or the user does not have the admin role
        404:
            description: The content type does not exist
        500:
            description: Error updating the content type (e.g. missing "slug" in the body)
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    # Get the request body
    body = request.json

    # Call the service to update the content type
    return services.update_type(body, username)

@bp.route('/get_type/<slug>', methods=['GET'])
@fernetAuthenticate
def get_type(username, isAdmin, slug):
    """
    Get the content type by its slug (delegates to app.api.types.services.get_by_slug)
    ---
    security:
        - JWT: []
    tags:
        - Admin API
    description: Requires an admin token.
    parameters:
        - in: path
          name: slug
          schema:
            type: string
          required: true
          description: Slug of the content type
    responses:
        200:
            description: Resource type retrieved successfully
        401:
            description: You do not have permission to retrieve the resource type
        404:
            description: The content type does not exist
        500:
            description: Error retrieving the resource type
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    # Call the service to get the resource type
    return services.get_type(slug, username)
