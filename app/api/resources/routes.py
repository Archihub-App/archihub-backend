from app.api.resources import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.resources import services
from app.api.users import services as user_services
from flask import request, jsonify
import json
from app.utils.functions import cache_type_roles
from flask_babel import _

# En este archivo se registran las rutas de la API para los recursos

# Nuevo endpoint para obtener todos los recursos dado un tipo de contenido y un body de filtros
@bp.route('/getall', methods=['POST'])
@jwt_required()
def get_all():
    """
    Get resources of one or more content types, paginated and filtered
    ---
    security:
        - JWT: []
    tags:
        - Resources
    parameters:
        - in: body
          name: body
          schema:
            type: object
            required:
                - post_type
            properties:
                post_type:
                    type: array
                    items:
                        type: string
                    description: Slugs of the content types to query (required)
                status:
                    type: string
                    enum: [published, draft, deleted]
                    default: published
                    description: "'deleted' requires the admin/editor/super_editor role (see can_view_deleted)"
                page:
                    type: integer
                    description: Page (internally multiplied by a fixed limit of 20)
                parents:
                    type: object
                    properties:
                        id:
                            type: string
                files:
                    type: boolean
                    description: If true, filters to only resources with associated files
                activeColumns:
                    type: array
                    items:
                        type: object
                        properties:
                            destiny:
                                type: string
                    description: Metadata columns to include in the response
                sortBy:
                    type: string
                    default: createdAt
                sortOrder:
                    type: string
                    enum: [asc, desc]
                    default: asc
    responses:
        200:
            description: "Object { total, resources } with the retrieved resources"
        401:
            description: Not authorized to view one of the requested content types, or requesting status=deleted without permission
        500:
            description: Error retrieving the resources (includes a KeyError if post_type is missing)
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Obtener el body del request
    body = request.json
    
    # convertir a cadena de texto el body
    body = json.dumps(body)

    # Llamar al servicio para obtener los recursos
    resp = services.get_all(body, current_user)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

# Nuevo endpoint para guardar un recurso nuevo
@bp.route('', methods=['POST'])
@jwt_required()
def create():
    """
    Create a new resource (multipart/form-data) and optionally upload its files
    ---
    security:
        - JWT: []
    tags:
        - Resources
    consumes:
        - multipart/form-data
    parameters:
        - in: formData
          name: data
          type: string
          required: true
          description: >
            Serialized JSON string (form field "data", not a JSON body) shaped as:
            { post_type: string (required), status: 'draft'|'published' (required;
            'published' requires the publisher or admin role), metadata: object (required,
            validated against the content type's schema), filesIds: string[]
            (required even if [], tags/order of the uploaded files), ident:
            string (optional, defaults to 'ident'), parents: object[] (optional, if
            sent validates that the resource is hierarchical) }
        - in: formData
          name: files
          type: file
          required: false
          description: Files to associate with the resource (repeatable "files" field)
    responses:
        201:
            description: "Resource created. Body: { msg, id, post_type }"
        400:
            description: Missing 'metadata', or field validation errors (body includes 'errors')
        401:
            description: Missing the admin/editor/super_editor role, missing the role required by the content type, or attempting to publish without the publisher/admin role
        500:
            description: Unexpected error creating the resource or its files (includes a KeyError for fields missing in 'data')
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'super_editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    # Obtener el body del request
    body = request.form.to_dict()
    data = body['data']
    # convertir data una cadena de texto JSON stringify a un diccionario
    data = json.loads(data)

    post_type = data['post_type']
    post_type_roles = cache_type_roles(post_type)
    if post_type_roles['editRoles']:
        canEdit = False
        for r in post_type_roles['editRoles']:
            if user_services.has_role(current_user, r) or user_services.has_role(current_user, 'admin'):
                canEdit = True
        if not canEdit:
            return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    files = request.files.getlist('files')

    # Llamar al servicio para crear el recurso
    return services.create(data, current_user, files)

# Nuevo endpoint para obtener un recurso por su id
@bp.route('/<id>', methods=['GET'])
@jwt_required()
def get_by_id(id):
    """
    Get a resource by its id
    ---
    security:
        - JWT: []
    tags:
        - Resources
    parameters:
        - in: path
          name: id
          type: string
          required: true
    responses:
        200:
            description: Resource retrieved successfully
        401:
            description: The resource is deleted and not authorized to view it, no access (accessRights), or missing the view role required by the content type
        404:
            description: Resource not found
        500:
            description: Error retrieving the resource
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Llamar al servicio para obtener el recurso
    resp = services.get_by_id(id, current_user)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/<id>', methods=['POST'])
@jwt_required()
def get_by_id_post(id):
    """
    Get a resource by its id (POST variant, equivalent to GET /<id>)
    ---
    security:
        - JWT: []
    tags:
        - Resources
    description: >
      Identical to GET /resources/{id}; it does not read or require any JSON body despite
      accepting POST (any body sent is ignored by the current implementation).
    parameters:
        - in: path
          name: id
          type: string
          required: true
    responses:
        200:
            description: Resource retrieved successfully
        401:
            description: The resource is deleted and not authorized to view it, no access (accessRights), or missing the view role required by the content type
        404:
            description: Resource not found
        500:
            description: Error retrieving the resource
    """
    body = request.json
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Llamar al servicio para obtener el recurso
    resp = services.get_by_id(id, current_user, True)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

# Nuevo endpoint para actualizar un recurso por su id
@bp.route('/<id>', methods=['PUT'])
@jwt_required()
def update_by_id(id):
    """
    Update a resource by its id (multipart/form-data)
    ---
    security:
        - JWT: []
    tags:
        - Resources
    consumes:
        - multipart/form-data
    parameters:
        - in: path
          name: id
          type: string
          required: true
        - in: formData
          name: data
          type: string
          required: true
          description: >
            Serialized JSON string (form field "data", not a JSON body) shaped as:
            { post_type: string (required), status: 'draft'|'published' (required;
            'published' requires the publisher or admin role), metadata: object (required),
            filesIds: string[] (required even if []), deletedFiles: string[]
            (required, ids of files to delete), updatedFiles: {id, order}[]
            (optional, reorders existing files), ident: string (optional),
            parents: object[] (optional) }
        - in: formData
          name: files
          type: file
          required: false
          description: New files to associate with the resource (repeatable "files" field)
    responses:
        200:
            description: "Resource updated. Body: { msg }"
        400:
            description: Error validating the resource's fields or files (body includes 'errors')
        401:
            description: Missing the admin/editor/super_editor role, not the resource's creator, missing the role required by the content type, or attempting to publish without the publisher/admin role
        500:
            description: Unexpected error updating the resource (includes a KeyError for fields missing in 'data')
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'super_editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    
    body = request.form.to_dict()
    data = body['data']
    # convertir data una cadena de texto JSON stringify a un diccionario
    data = json.loads(data)

    post_type = data['post_type']
    post_type_roles = cache_type_roles(post_type)
    if post_type_roles['editRoles']:
        canEdit = False
        for r in post_type_roles['editRoles']:
            if user_services.has_role(current_user, r) or user_services.has_role(current_user, 'admin'):
                canEdit = True
        if not canEdit:
            return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    files = request.files.getlist('files')
    # Llamar al servicio para crear el recurso
    resp = services.update_by_id(id, data, current_user, files)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp
    # return 'ok'

@bp.route('/<id>/granular', methods=['PUT'])
@jwt_required()
def update_granular_by_id(id):
    """
    Update a metadata field across all parent resources of a record (file)
    ---
    security:
        - JWT: []
    tags:
        - Resources
    description: >
      Despite living under /resources, {id} is the id of a RECORD (file), not a
      resource: that record is looked up, its 'parent' resources are taken, and the
      given metadata field is updated on each of them (only text/text-area
      field types).
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: Id of the record (file) whose parent resources will be updated
        - in: body
          name: body
          schema:
            type: object
            required:
                - metadataPath
            properties:
                metadataPath:
                    type: string
                    description: Dotted path within 'metadata' (e.g. 'firstLevel.title'); required
                value:
                    type: string
                    default: ""
                    description: Must be a string; any other type is rejected
                concat:
                    type: boolean
                    default: false
                    description: If true, concatenates the value instead of replacing it
    responses:
        200:
            description: "Metadata updated. Body: { msg, updated, resources }"
        400:
            description: Missing metadataPath, value is not a string, or no parent resource could be updated (due to authorization, schema, or not found)
        401:
            description: Missing the admin/editor/super_editor role
        404:
            description: The record does not exist or has no parent resources
        500:
            description: Unexpected error updating the metadata
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    # Si el usuario no es admin, editor o super_editor, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'super_editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    body = request.json or {}
    metadata_path = body.get('metadataPath')
    value = body.get('value', '')
    concat = body.get('concat', False)

    resp = services.update_granular_by_id(id, metadata_path, value, current_user, concat)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp
    
@bp.route('/updateorder/<id>', methods=['POST'])
@jwt_required()
def update_file_order(id):
    """
    Reorder a resource's files by its id
    ---
    security:
        - JWT: []
    tags:
        - Resources
    parameters:
        - in: path
          name: id
          type: string
          required: true
        - in: body
          name: body
          schema:
            type: object
            properties:
                files:
                    type: array
                    description: New target position for some/all files (actual field name; not "filesOrder")
                    items:
                        type: object
                        properties:
                            id:
                                type: string
                            order:
                                type: integer
    responses:
        200:
            description: File order updated successfully
        401:
            description: Missing the admin/editor/super_editor role, or no access (accessRights) to the resource
        404:
            description: Resource not found
        500:
            description: Error updating the file order
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'super_editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    
    body = request.json

    # Llamar al servicio para actualizar el orden de los archivos
    resp = services.update_files_order(id, body, current_user)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

# Nuevo endpoint para eliminar recursos por arreglo de ids
@bp.route('', methods=['DELETE'])
@jwt_required()
def delete_by_id():
    """
    Delete (soft-delete) one or more resources by id
    ---
    security:
        - JWT: []
    tags:
        - Resources
    parameters:
        - in: body
          name: body
          schema:
            type: array
            items:
                type: string
          description: Array of resource ids (required, all must be strings)
    responses:
        200:
            description: "Resources deleted. Body: { msg, ids }"
        400:
            description: The body is not an array, or contains elements that are not strings
        401:
            description: Missing the admin/editor/super_editor role, or missing the role required by the content type of one of the resources
        404:
            description: One of the resources does not exist
        500:
            description: Unexpected error deleting the resources
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, editor o super_editor, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'super_editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    body = request.json
    if not isinstance(body, list):
        return jsonify({'msg': _('Body must be an array of resource ids')}), 400

    if any(not isinstance(item, str) for item in body):
        return jsonify({'msg': _('Body must be an array of string ids')}), 400

    return services.delete_by_id(body, current_user)

# Nuevo endpoint para restaurar recursos por arreglo de ids
@bp.route('/restore', methods=['POST'])
@jwt_required()
def restore_by_id():
    """
    Restore deleted resources by an array of ids
    ---
    security:
        - JWT: []
    tags:
        - Resources
    parameters:
        - in: body
          name: body
          schema:
            type: object
            required:
                - ids
            properties:
                ids:
                    type: array
                    items:
                        type: string
                    description: Required; array of resource ids to restore
                recursive:
                    type: boolean
                    default: false
                    description: If true, also restores deleted child resources
    responses:
        200:
            description: "Resources restored. Body: { msg, ids }"
        400:
            description: The body is not an object, or ids/recursive have an invalid type
        401:
            description: Missing the admin role
        500:
            description: Unexpected error restoring resources
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    body = request.json
    if not isinstance(body, dict):
        return jsonify({'msg': _('Body must be an object with ids and recursive')}), 400

    ids = body.get('ids')
    recursive = body.get('recursive', False)

    if not isinstance(ids, list):
        return jsonify({'msg': _('ids must be an array of resource ids')}), 400

    if any(not isinstance(item, str) for item in ids):
        return jsonify({'msg': _('ids must be an array of string ids')}), 400

    if not isinstance(recursive, bool):
        return jsonify({'msg': _('recursive must be a boolean')}), 400

    return services.restore_by_id(ids, current_user, recursive)

# Nuevo endpoint para obtener las estructura de arból de un tipo de contenido y sus recursos
@bp.route('/tree', methods=['POST'])
@jwt_required()
def get_tree():
    """
    Get the resource tree, in two modes: 'tree' (hierarchical navigation) or 'list' (flat paginated listing)
    ---
    security:
        - JWT: []
    tags:
        - Resources
    description: >
      Requires 'view' in the body ('tree' or 'list'); any other value (or its
      absence) causes the route to return no response (fails with 500 when
      trying to serialize None).
    parameters:
        - in: body
          name: body
          schema:
            type: object
            required:
                - view
                - root
            properties:
                view:
                    type: string
                    enum: [tree, list]
                root:
                    type: string
                    description: Id of the root resource, or 'all' for the top level
                tree:
                    type: array
                    description: Required if view=tree; list of { slug } filtered by view role
                    items:
                        type: object
                        properties:
                            slug:
                                type: string
                postType:
                    type: string
                    description: view=list; if sent (non-empty), used together with its parent types instead of activeTypes
                activeTypes:
                    type: array
                    items:
                        type: string
                    description: view=list; required if postType is not sent or is empty
                status:
                    type: string
                    enum: [published, draft, deleted]
                    default: published
                    description: view=list; 'draft' requires the editor or admin role
                page:
                    type: integer
                    description: view=list; optional, fixed page size of 10
    responses:
        200:
            description: view=tree -> array of nodes; view=list -> array of resources
        401:
            description: Missing the view role required by one of the content types, or requesting status=draft without the editor/admin role
        500:
            description: Unexpected error (includes a KeyError if required fields are missing, or 'view' missing/unrecognized)
    """
    try:
        # Obtener el usuario actual
        current_user = get_jwt_identity()
        # Obtener el body del request
        body = request.json

        if 'view' in body:
            if body['view'] == 'tree':
                slugs = [item['slug'] for item in body['tree']]
                return_slugs = []

                for s in slugs:
                    roles = cache_type_roles(s)
                    if roles['viewRoles']:
                        for r in roles['viewRoles']:
                            if user_services.has_role(current_user, r) or user_services.has_role(current_user, 'admin'):
                                return_slugs.append(s)
                    else:
                        return_slugs.append(s)

                # Llamar al servicio para obtener la estructura de arból
                resp = services.get_tree(body['root'],'|'.join(return_slugs), current_user)
                
                if isinstance(resp, list):
                    resp = tuple(resp)
                
                return resp
        
            elif body['view'] == 'list':
                if 'postType' in body:
                    if body['postType']:
                        type = body['postType']
                        from app.api.types.services import get_by_slug
                        type = get_by_slug(type)
                        if isinstance(type, list):
                            type = type[0]
                        from app.api.types.services import get_parents
                        parents = get_parents(type)
                        
                        slugs = [item['slug'] for item in parents]
                        slugs = [type['slug'], *slugs]
                    else:
                        slugs = body['activeTypes']
                elif 'root' in body:
                    slugs = body['activeTypes']

                return_slugs = []
                
                if not 'status' in body:
                    body['status'] = 'published'
                if body['status'] == 'draft':
                    if not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'admin'):
                        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

                for s in slugs:
                    roles = cache_type_roles(s)
                    if roles['viewRoles']:
                        for r in roles['viewRoles']:
                            if user_services.has_role(current_user, r) or user_services.has_role(current_user, 'admin'):
                                return_slugs.append(s)
                    else:
                        return_slugs.append(s)
                
                resp = services.get_tree(body['root'],'|'.join(return_slugs), current_user, body['postType'] if 'postType' in body else None, int(body['page']) if 'page' in body else 0, body['status'] if 'status' in body else 'published')

                if isinstance(resp, list):
                    resp = tuple(resp)
                
                return resp
    except Exception as e:
        print(str(e))
        return jsonify({'msg': str(e)}), 500
        
# Nuevo endpoint para obtener los recursos de un recurso padre
@bp.route('/<resource_id>/records', methods=['POST'])
@jwt_required()
def get_all_records(resource_id):
    """
    Get (paginated) the files associated with a resource
    ---
    security:
        - JWT: []
    tags:
        - Resources
    parameters:
        - in: path
          name: resource_id
          type: string
          required: true
        - in: body
          name: body
          schema:
            type: object
            required:
                - page
            properties:
                page:
                    type: integer
                    description: Required (page number, no default)
                groupImages:
                    type: boolean
                    description: If true, groups images into a single gallery entry
    responses:
        200:
            description: "Files retrieved. Body: { data, total }"
        401:
            description: No access (accessRights) to the resource and missing the admin role
        404:
            description: Resource not found
        500:
            description: Error retrieving the files (includes a KeyError if 'page' is missing)
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    body = request.json

    if 'groupImages' not in body:
        resp = services.get_resource_files(resource_id, current_user, body['page'])
    else:
        resp = services.get_resource_files(resource_id, current_user, body['page'], body['groupImages'])
        
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp
    
@bp.route('/<resource_id>/article', methods=['GET'])
@jwt_required()
def get_article_body(resource_id):
    """
    Get the article body of a resource
    ---
    security:
        - JWT: []
    tags:
        - Resources
    parameters:
        - in: path
          name: resource_id
          type: string
          required: true
    responses:
        200:
            description: "Article body retrieved. Body: { articleBody }"
        401:
            description: Missing editor/admin role, or no access (accessRights) to the resource
        404:
            description: Resource not found
        500:
            description: Error retrieving the article body
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    # Llamar al servicio para obtener el cuerpo del artículo
    resp = services.get_article_body(resource_id, current_user)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/<resource_id>/article', methods=['POST'])
@jwt_required()
def update_article_body(resource_id):
    """
    Update the article body of a resource
    ---
    security:
        - JWT: []
    tags:
        - Resources
    parameters:
        - in: path
          name: resource_id
          type: string
          required: true
        - in: body
          name: body
          schema:
            type: object
            required:
                - articleBody
            properties:
                articleBody:
                    type: array
                    description: Required (cannot be null/omitted); list of article blocks
    responses:
        200:
            description: Article body updated successfully
        400:
            description: Missing articleBody
        401:
            description: Missing editor/admin role
        404:
            description: Resource not found
        500:
            description: Error updating the article body
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    body = request.json

    if not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    # Llamar al servicio para actualizar el cuerpo del artículo
    resp = services.update_article_body(resource_id, body, current_user)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp 

@bp.route('/<resource_id>/article/comments', methods=['POST'])
@jwt_required()
def add_article_block_comment(resource_id):
    """
    Save a comment on a block of the article body
    ---
    security:
        - JWT: []
    tags:
        - Resources
    parameters:
        - in: path
          name: resource_id
          type: string
          required: true
        - in: body
          name: body
          schema:
            type: object
            required:
                - comment
            properties:
                comment:
                    type: string
                    description: Required, cannot be empty/whitespace-only
                blockIndex:
                    type: integer
                    description: Block index (one of blockIndex or blockId is required)
                blockId:
                    type: string
                    description: Block id (one of blockIndex or blockId is required)
    responses:
        200:
            description: "Comment saved. Body: { msg, blockIndex, blockId, comment }"
        400:
            description: Missing comment, missing blockIndex/blockId, blockIndex is not an integer, or the articleBody/block/comments have an invalid format
        401:
            description: Missing editor/admin role
        404:
            description: Resource or block not found
        500:
            description: Error saving the comment
    """
    current_user = get_jwt_identity()
    body = request.json or {}

    if not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    resp = services.add_article_block_comment(resource_id, body, current_user)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/download_records', methods=['POST'])
@jwt_required()
def download_all_records():
    """
    Download the file(s) of a resource (single file, or a zip if there are several)
    ---
    security:
        - JWT: []
    tags:
        - Resources
    parameters:
        - in: body
          name: body
          schema:
            type: object
            required:
                - id
                - type
            properties:
                id:
                    type: string
                    description: Resource id (required)
                type:
                    type: string
                    enum: [original, small]
                    description: Required; which file variant to download
    produces:
        - application/octet-stream
    responses:
        200:
            description: Binary file (attachment); a single file directly, or a .zip if the resource has more than one
        400:
            description: The 'files_download' capability is not active in the system configuration
        401:
            description: No access (accessRights) to the resource and missing the admin role
        404:
            description: The resource or one of its files does not exist
        500:
            description: Unexpected error generating the download (includes a KeyError if 'id'/'type' are missing)
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    body = request.json

    return services.download_resource_files(body, current_user)

   
@bp.route('/<resource_id>/imgs', methods=['GET'])
@jwt_required()
def get_imgs(resource_id):
    """
    Get the images associated with a resource (gallery)
    ---
    security:
        - JWT: []
    tags:
        - Resources
    parameters:
        - in: path
          name: resource_id
          type: string
          required: true
    responses:
        200:
            description: Images retrieved successfully
        404:
            description: Resource not found, or has no associated images
        500:
            description: Error retrieving the images
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    # Llamar al servicio para obtener los recursos
    resp = services.get_resource_images(resource_id, current_user)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp
    
@bp.route('/favcount/<resource_id>', methods=['GET'])
@jwt_required()
def favcount(resource_id):
    """
    Get the favorites count of a resource
    ---
    security:
        - JWT: []
    tags:
        - Resources
    description: Does not validate role or accessRights beyond requiring a valid JWT.
    parameters:
        - in: path
          name: resource_id
          type: string
          required: true
    responses:
        200:
            description: "Count retrieved. Body: { favCount }"
        500:
            description: Error retrieving the favorites count (includes a nonexistent resource)
    """
    # Llamar al servicio para obtener el contador de favoritos
    return services.get_favCount(resource_id)


@bp.route('/change-post-type', methods=['POST'])
@jwt_required()
def change_post_type():
    """
    Validate editing permissions over a resource's current content type
    ---
    security:
        - JWT: []
    tags:
        - Resources
    description: >
      Despite the name, the current implementation only validates that the user
      has an editing role over the current post_type of the resource indicated
      by 'id'; it does not persist any post_type change to the database.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            required:
                - id
            properties:
                id:
                    type: string
                    description: Required; resource id
                post_type:
                    type: string
                    description: Accepted by the API but not used by the current implementation
    responses:
        200:
            description: Verification successful
        401:
            description: Missing admin/editor role over the resource's current content type
        500:
            description: Unexpected error (includes a KeyError if 'id' is missing, or a nonexistent resource)
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.json
    # Llamar al servicio para cambiar el tipo de contenido
    return services.change_post_type(body, current_user)