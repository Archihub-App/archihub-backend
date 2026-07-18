from app.api.logs import bp
from flask import jsonify
from flask import request
from . import services
from app.api.users import services as user_services
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from flask_babel import _
from app.utils.FernetAuth import fernetAuthenticate
# En este archivo se registran las rutas de la API para los logs


@bp.route('/actions', methods=['GET'])
@jwt_required()
def get_log_actions():
    """
    Get the catalog of available log actions (LogActions.log_actions)
    ---
    security:
        - JWT: []
    tags:
        - System logs
    responses:
        200:
            description: Dictionary of available log actions (always 200, does not depend on the database)
        401:
            description: You don't have permission to perform this action (the authenticated user doesn't have the admin role)
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para obtener las acciones de log
    return services.get_log_actions()

# Nuevo POST endpoint para obtener los logs de acuerdo a un filtro
@bp.route('', methods=['POST'])
@jwt_required()
def filter():
    """
    Get logs according to a filter (paginated)
    ---
    security:
        - JWT: []
    tags:
        - System logs
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
              filters:
                type: object
                description: 'Mongo filter applied directly to the "logs" collection (e.g. username, action).'
              page:
                type: integer
                description: Result page (20 per page, skip = page * 20).
            required:
              - filters
              - page
    responses:
        200:
            description: >
                Array of logs (with "details" derived from metadata and "total" appended to each item).
                Note: due to a known bug in the service (filter() evaluates "if not logs" on a pymongo
                cursor, which is always truthy), the absence of results NEVER produces a 404: it always
                responds 200, with an empty array if there are no matches.
        401:
            description: You don't have permission to perform this action (the authenticated user doesn't have the admin role)
        500:
            description: Error retrieving logs (includes the case where "filters" or "page" are missing from the body)
    """
    # Obtener el body del request
    body = request.json
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para obtener los logs de acuerdo a un filtro
    return services.filter(body)

# Nuevo GET endpoint para obtener todos los logs de un recurso
@bp.route('/resource/<resource_id>', methods=['POST'])
@jwt_required()
def get_logs(resource_id):
    """
    Get the change history (diffs) of a resource, derived from its RESOURCE_CREATE/RESOURCE_UPDATE logs
    ---
    security:
        - JWT: []
    tags:
        - System logs
    parameters:
        - in: path
          name: resource_id
          required: true
          type: string
          description: Resource ID (metadata.resource._id in the logs)
        - in: body
          name: body
          schema:
                type: object
                properties:
                    page:
                        type: integer
                        description: Result page (20 per page, skip = page * 20). Optional, defaults to skip=0.
    responses:
        200:
            description: >
                Array of detected changes (path/date/old/new) comparing consecutive pairs of RESOURCE_CREATE/
                RESOURCE_UPDATE logs for the resource. Empty if there are fewer than two logs to compare.
                Note: due to the same "if not logs" bug on a pymongo cursor described in POST /logs, the
                absence of logs for the resource NEVER produces a 404: it always responds 200 (with [] if
                there are no logs, or if there aren't at least two to compare changes).
        401:
            description: You don't have permission to perform this action (the authenticated user doesn't have the admin role)
        500:
            description: Error retrieving logs
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    
    # Obtener el body del request
    body = request.json

    # Llamar al servicio para obtener los logs de un recurso
    return services.get_logs(body, resource_id)
