from app.api.resources import bp
from app.api.resources import public_services
from flask import request, jsonify
import json
from app.utils.functions import cache_type_roles

@bp.route('/getall/public', methods=['POST'])
def get_all_public():
    """
    Get published resources of one or more content types (no authentication)
    ---
    tags:
        - Resources
    description: >
      Does not require JWT. Only returns resources with status='published'; if any of
      the requested post_type values has viewRoles configured (restricted access),
      the whole request is rejected with 401.
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
                    description: Required; slugs of the content types to query
                page:
                    type: integer
                    description: Page (fixed limit of 20)
                parents:
                    type: object
                    properties:
                        id:
                            type: string
                files:
                    type: boolean
                    description: If true, filters only resources with associated files
                activeColumns:
                    type: array
                    items:
                        type: object
                        properties:
                            destiny:
                                type: string
                sortBy:
                    type: string
                    default: createdAt
                sortOrder:
                    type: string
                    enum: [asc, desc]
                    default: asc
    responses:
        200:
            description: "Object { total, resources } with the published resources"
        401:
            description: One of the requested post_type values has viewRoles (not public)
        500:
            description: Error retrieving resources (includes KeyError if post_type is missing)
    """
    body = request.json
    body = json.dumps(body)
    resp = public_services.get_all(body)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/public/<id>', methods=['GET'])
def get_by_id_public(id):
    """
    Get a published resource by its id (no authentication)
    ---
    tags:
        - Resources
    description: Does not require JWT. Only returns the resource if it's publicly accessible (no restrictive accessRights or viewRoles).
    parameters:
        - in: path
          name: id
          type: string
          required: true
    responses:
        200:
            description: Resource retrieved successfully
        401:
            description: The resource has accessRights or viewRoles that restrict public access
        404:
            description: Resource not found
        500:
            description: Error retrieving the resource
    """
    resp = public_services.get_by_id(id)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/public/<resource_id>/records', methods=['POST'])
def get_all_records_public(resource_id):
    """
    Get (paginated) the files of a published resource (no authentication)
    ---
    tags:
        - Resources
    description: Does not require JWT. Only works if the resource is publicly accessible (no restrictive accessRights).
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
            description: The resource has accessRights that restrict public access
        404:
            description: Resource not found
        500:
            description: Error retrieving the files (includes KeyError if 'page' is missing)
    """
    body = request.json

    if 'groupImages' not in body:
        resp = public_services.get_resource_files(resource_id, body['page'])
    else:
        resp = public_services.get_resource_files(resource_id, body['page'], body['groupImages'])
        
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/public/tree', methods=['POST'])
def get_tree_public():
    """
    Get the tree of published resources, without authentication ('tree' or 'list')
    ---
    tags:
        - Resources
    description: >
      Does not require JWT. Only includes content types with no viewRoles configured
      (those that do are silently omitted, not rejected with 401). Requires
      'view' in the body ('tree' or 'list'); any other value (or its absence)
      causes the route to return no response (fails with 500).
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
                    description: Required if view=tree; list of { slug }
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
                page:
                    type: integer
                    description: view=list; optional, fixed page size of 10
    responses:
        200:
            description: view=tree -> array of nodes; view=list -> array of published resources
        500:
            description: Unexpected error (includes KeyError if required fields are missing, or 'view' missing/unrecognized)
    """
    try:
        body = request.json

        if 'view' in body:
            if body['view'] == 'tree':
                slugs = [item['slug'] for item in body['tree']]
                return_slugs = []

                for s in slugs:
                    roles = cache_type_roles(s)
                    if not roles['viewRoles']:
                        return_slugs.append(s)

                # Llamar al servicio para obtener la estructura de arból
                resp = public_services.get_tree(body['root'],'|'.join(return_slugs))
                
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

                for s in slugs:
                    roles = cache_type_roles(s)
                    if not roles['viewRoles']:
                        return_slugs.append(s)
                
                resp = public_services.get_tree(body['root'],'|'.join(return_slugs), body['postType'] if 'postType' in body else None, int(body['page']) if 'page' in body else 0)

                if isinstance(resp, list):
                    resp = tuple(resp)
                
                return resp
    except Exception as e:
        return jsonify({'msg': str(e)}), 500

@bp.route('/public/<resource_id>/imgs', methods=['GET'])
def get_imgs_public(resource_id):
    """
    Get the images of a published resource (no authentication)
    ---
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
    # Llamar al servicio para obtener los recursos
    resp = public_services.get_resource_images(resource_id)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp
    
@bp.route('/public/download_records', methods=['POST'])
def download_public():
    """
    Download the file(s) of a published resource (no authentication)
    ---
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
                    description: Resource id (required; must be published)
                type:
                    type: string
                    enum: [original, small]
                    description: Required; which file variant to download
    produces:
        - application/octet-stream
    responses:
        200:
            description: Binary file (attachment); a single direct file, or a .zip if the resource has more than one
        401:
            description: The resource has accessRights that restrict public access
        404:
            description: The resource doesn't exist (or isn't published), or one of its files doesn't exist
        500:
            description: Unexpected error generating the download (includes KeyError if 'id'/'type' are missing)
    """
    body = request.json
    
    return public_services.download_resource_files(body)