from app.api.types import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.types import services
from app.api.users import services as user_services
from flask import request
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.utils.functions import cache_type_roles
from app.utils import DatabaseHandler
from flask_babel import _

mongodb = DatabaseHandler.DatabaseHandler()

# En este archivo se registran las rutas de la API para los tipos de contenido

# Nuevo endpoint para obtener todos los tipos de contenido
@bp.route('', methods=['GET'])
@jwt_required()
def get_all():
    """
    Get all cataloging content types
    ---
    security:
        - JWT: []
    tags:
        - Content types
    description: No additional role restriction beyond having a valid session (401 only if the JWT token is missing or invalid). Returns only name, description, and slug for each type.
    responses:
        200:
            description: List of content types (name, description, slug only)
        401:
            description: JWT token missing or invalid
        500:
            description: Error retrieving the content types
    """
    # Llamar al servicio para obtener todos los tipos de contenido
    resp = services.get_all()
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

# Nuevo endpoint para crear un tipo de contenido
@bp.route('', methods=['POST'])
@jwt_required()
def create():
    """
    Create a new content type with the request body
    ---
    security:
        - JWT: []
    tags:
        - Content types
    description: Requires the admin role. slug is a required key in the body (may be sent empty to auto-generate it from name); if a non-empty slug is sent that already exists, creation fails with 400.
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
                    description: Required key; send "" to auto-generate the slug from name.
                metadata:
                    type: string
                    description: Slug of the form (forms) associated as the type's metadata.
                icon:
                    type: string
                hierarchical:
                    type: boolean
                parentType:
                    type: array
                    items:
                        type: object
                isArticle:
                    type: boolean
            required:
                - name
                - description
                - slug
    responses:
        201:
            description: Content type created
        400:
            description: The slug already exists, or name/slug are empty
        401:
            description: You don't have permission to create a content type (not admin)
        500:
            description: Error creating the content type
    """

    # Obtener el body de la request
    body = request.json

    # Obtener el usuario actual
    current_user = get_jwt_identity()

    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    
    # Si el slug no está definido, crearlo
    if not body['slug'] or body['slug'] == '':
        body['slug'] = body['name'].lower().replace(' ', '-')
        # quitamos los caracteres especiales y las tildes pero dejamos los guiones
        body['slug'] = ''.join(e for e in body['slug'] if e.isalnum() or e == '-')
        # quitamos los guiones al inicio y al final
        body['slug'] = body['slug'].strip('-')
        # quitamos los guiones repetidos
        body['slug'] = body['slug'].replace('--', '-')

        # Llamar al servicio para obtener un tipo de contenido por su slug
        slug_exists = mongodb.get_record('post_types', {'slug': body['slug']}, {'slug': 1})
        
        # Mientras el slug exista, agregar un número al final
        index = 1
        begin_slug = body['slug']
        while slug_exists:
            body['slug'] = begin_slug + '-' + str(index)
            slug_exists = mongodb.get_record('post_types', {'slug': body['slug']}, {'slug': 1})
            index += 1

        # Llamar al servicio para crear un tipo de contenido
        return services.create(body, current_user)
    else:
        slug_exists = mongodb.get_record('post_types', {'slug': body['slug']}, {'slug': 1})
        if not slug_exists:
            return services.create(body, current_user)
        else:
            return {'msg': _('Slug already exists')}, 400

# Nuevo endpoint para obtener un tipo de contenido por su slug
@bp.route('/<slug>', methods=['GET'])
@jwt_required()
def get_by_slug(slug):
    """
    Get a content type by its slug
    ---
    security:
        - JWT: []
    tags:
        - Content types
    description: Requires the admin or editor role. If the type has viewRoles configured, the user is additionally required to have admin or one of those roles. The response includes parentsTypes (parent hierarchy) and, if it has associated metadata, the resolved form.
    parameters:
        - in: path
          name: slug
          type: string
          required: true
    responses:
        200:
            description: Content type
        401:
            description: You don't have permission to retrieve this content type
        404:
            description: Content type does not exist
        500:
            description: Error retrieving the content type (includes errors resolving the metadata form)
    """
    # se obtiene el usuario actual
    current_user = get_jwt_identity()
    # se verifica si el usuario tiene el rol de administrador o editor
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    
    roles = cache_type_roles(slug)
    if roles['viewRoles']:
        canView = False
        for r in roles['viewRoles']:
            if user_services.has_role(current_user, r) or user_services.has_role(current_user, 'admin'):
                canView = True
                break
        if not canView:
            return {'msg': _('You don\'t have the required authorization')}, 401
            
    # Llamar al servicio para obtener un tipo de contenido por su slug
    slug_exists = services.get_by_slug(slug)
    # si el service.get_by_slug devuelve un error, entonces el tipo de contenido no existe
    if 'msg' in slug_exists:
        if slug_exists['msg'] == _('Type not found'):
            return slug_exists, 404
    else:
        return slug_exists

# Nuevo endpoint para actualizar un tipo de contenido por su slug
@bp.route('/<slug>', methods=['PUT'])
@jwt_required()
def update_by_slug(slug):
    """
    Update a content type by its slug
    ---
    security:
        - JWT: []
    tags:
        - Content types
    description: Requires the admin or editor role. parentType must always be sent (even as an empty list); if omitted from the body, the request fails with 500 due to an internal KeyError. If parentType includes the type's own slug as a parent, it is automatically removed from the list to prevent a direct cycle. editRoles/viewRoles are validated against the existing roles.
    parameters:
        - in: path
          name: slug
          schema:
            type: string
          required: true
          description: slug of the content type to update
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
                description:
                    type: string
                icon:
                    type: string
                hierarchical:
                    type: boolean
                parentType:
                    type: array
                    items:
                        type: object
                metadata:
                    type: string
                editRoles:
                    type: array
                    items:
                        type: string
                viewRoles:
                    type: array
                    items:
                        type: string
                isArticle:
                    type: boolean
    responses:
        200:
            description: Content type updated
        401:
            description: You don't have permission to update a content type (not admin or editor)
        404:
            description: Content type does not exist
        500:
            description: Error updating the content type (includes nonexistent roles in editRoles/viewRoles)
    """
    # se obtiene el usuario actual
    current_user = get_jwt_identity()
    # se verifica si el usuario tiene el rol de administrador o editor
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Obtener el body de la request
    body = request.json
    # Llamar al servicio para actualizar un tipo de contenido por su slug
    return services.update_by_slug(slug, body, current_user)
            

# Nuevo endpoint para eliminar un tipo de contenido por su slug
@bp.route('/<slug>', methods=['DELETE'])
@jwt_required()
def delete_by_slug(slug):
    """
    Delete a content type by its slug
    ---
    security:
        - JWT: []
    tags:
        - Content types
    description: Requires the admin or editor role. Does not physically delete the associated resources; it marks them with status "deleted".
    parameters:
        - in: path
          name: slug
          schema:
            type: string
          required: true
          description: slug of the content type to delete
    responses:
        200:
            description: Content type deleted (and its resources marked as deleted)
        401:
            description: You don't have permission to delete a content type (not admin or editor)
        404:
            description: Content type does not exist
        500:
            description: Error deleting the content type
    """
    # se obtiene el usuario actual
    current_user = get_jwt_identity()
    # se verifica si el usuario tiene el rol de administrador o editor
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Llamar al servicio para eliminar un tipo de contenido por su slug
    resp = services.delete_by_slug(slug, current_user)
    if isinstance(resp, dict):
        return resp
    else:
        return tuple(resp)
    
@bp.route('/moreinfo', methods=['POST'])
@jwt_required()
def get_type_viz():
    """
    Get visualization statistics (charts) for a content type
    ---
    security:
        - JWT: []
    tags:
        - Content types
    description: >
      Requires the admin or editor role. type accepts timeCreated (count by
      creation date), statusCount (count by status), or authorCount (top 10 by
      createdBy); any other type value returns an ok message with no data.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                slug:
                    type: string
                    description: slug of the content type
                type:
                    type: string
                    enum: [timeCreated, statusCount, authorCount]
            required:
                - slug
                - type
    responses:
        200:
            description: Aggregated data for the requested chart
        400:
            description: Missing slug or type in the body
        401:
            description: You don't have permission for this action (not admin or editor)
        500:
            description: Error retrieving the information
    """
    body = request.get_json()

    if 'slug' not in body or 'type' not in body:
        return {'msg': _('You must specify the slug and the type')}, 400
    
    if not user_services.has_role(get_jwt_identity(), 'admin') and not user_services.has_role(get_jwt_identity(), 'editor'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    
    resp = services.get_type_viz(body['slug'], body['type'])

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp