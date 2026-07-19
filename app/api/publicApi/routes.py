from app.api.publicApi import bp
from flask import jsonify
from flask import request
from app.utils.FernetAuth import publicFernetAuthenticate as fernetAuthenticate
import json

@bp.route('', methods=['POST'])
@fernetAuthenticate
def get_all(username, isAdmin):
    """
    Get published resources: paginated listing by type, or keyword search
    ---
    security:
        - JWT: []
    tags:
        - Public Api
    description: >
        Requires an encrypted Fernet token from the Public Api (header `Authorization: Bearer <token>`,
        distinct from the normal JWT from `/auth/login`; see app.utils.FernetAuth.publicFernetAuthenticate).
        If `keyword` is present and non-empty in the body, the search is delegated to
        app.api.search.public_services.get_resources_by_filters (Elasticsearch or vector DB,
        depending on the system's active capabilities). Otherwise it is delegated to
        app.api.resources.public_services.get_all, which requires `post_type` (list of slugs) and
        only returns resources with `status: "published"`.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                keyword:
                    type: string
                    description: If non-empty, activates the search flow instead of the listing
                searchSource:
                    type: string
                    description: "'index' (Elasticsearch, default) or 'vector' — only applies with keyword"
                post_type:
                    type: array
                    items:
                        type: string
                    description: Required when there is no keyword; slugs of the content types to list
                page:
                    type: integer
                activeColumns:
                    type: array
                    items:
                        type: object
                parents:
                    type: object
                    description: "{'id': ...} to filter by direct parent"
                files:
                    type: boolean
                    description: If true, filters only resources that have associated files
                sortBy:
                    type: string
                    default: createdAt
                sortOrder:
                    type: string
                    default: asc
    responses:
        200:
            description: Resources retrieved successfully
        401:
            description: A requested content type has a view-role restriction the user does not meet
        500:
            description: Error retrieving the resources (e.g. missing "post_type" in the body without keyword)
    """
    body = request.json

    if 'keyword' in body and body['keyword'] != '':
      from app.api.search.public_services import get_resources_by_filters
      resp = get_resources_by_filters(body)
    else:
      from app.api.resources.public_services import get_all
      resp = get_all(json.dumps(body))

    if isinstance(resp, list):
      return tuple(resp)
    else:
      return resp

@bp.route('/types', methods=['GET'])
@fernetAuthenticate
def get_types(username, isAdmin):
    """
    Get all content types (delegates to app.api.types.services.get_all)
    ---
    security:
        - JWT: []
    tags:
        - Public Api
    description: Requires an encrypted Fernet token from the Public Api (see the POST /publicApi description).
    responses:
        200:
            description: Resources retrieved successfully
        500:
            description: Error retrieving the resources
    """
    from app.api.types.services import get_all as get_all_types
    resp = get_all_types()

    if isinstance(resp, list):
      return tuple(resp)
    else:
      return resp

@bp.route('/resources/<id>', methods=['GET'])
@fernetAuthenticate
def get_item(username, isAdmin, id):
    """
    Get a published resource by its ID (delegates to app.api.resources.public_services.get_by_id)
    ---
    security:
        - JWT: []
    tags:
        - Public Api
    description: Requires an encrypted Fernet token from the Public Api (see the POST /publicApi description).
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: MongoDB ObjectId of the resource
    responses:
        200:
            description: Resource retrieved successfully
        401:
            description: Token not provided/invalid/expired, or the resource has accessRights/viewRoles the public user does not meet
        500:
            description: >
                Error retrieving the resource. Note: a nonexistent or unpublished id also
                falls here with 500 (generic "Resource does not exist" exception), not a dedicated 404.
    """
    from app.api.resources.public_services import get_by_id as get_by_id_public
    resp =  get_by_id_public(id)
    
    if isinstance(resp, list):
      return tuple(resp)
    else:
      return resp