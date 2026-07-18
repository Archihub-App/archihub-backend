from app.api.tasks import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.tasks import services
from app.api.users import services as user_services
from flask import request, jsonify
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from flask_babel import _

# from app.tasks.tasks import add
from celery.result import AsyncResult

from flask import current_app as app

@bp.route('/<user>', methods=['POST'])
@jwt_required()
def get_tasks(user):
    """
    Get a user's tasks (paginated, 10 per page)
    ---
    security:
        - JWT: []
    tags:
        - Processing Tasks
    parameters:
        - in: path
          name: user
          required: true
          type: string
          description: >-
              Username, or the literal "automatic" for system-generated
              tasks (requires the admin role, unless the authenticated
              user is "automatic" itself)
        - in: body
          name: body
          required: true
          description: >-
              Required JSON body (may be empty, `{}`); all keys are
              optional.
          schema:
            type: object
            properties:
                page:
                    type: integer
                    description: 'Result page, 0-indexed (10 tasks per page); defaults to 0 if omitted'
                automatic:
                    description: >-
                        If this key is present (any value), the "automatic"
                        user's tasks are listed instead of the ones for the
                        `user` path param
    responses:
        200:
            description: List of tasks
        401:
            description: Not authorized to get the tasks
        500:
            description: Error retrieving the tasks
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    body = request.json
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin') and (current_user != user and user == 'automatic'):
        return {'msg': _('You don\'t have the required authorization')}, 401

    return services.get_tasks(user, body)

@bp.route('/total/<user>', methods=['GET'])
@jwt_required()
def get_tasks_total(user):
    """
    Get a user's total task count
    ---
    security:
        - JWT: []
    tags:
        - Processing Tasks
    parameters:
        - in: path
          name: user
          required: true
          type: string
          description: Username
    responses:
        200:
            description: Total task count
        401:
            description: Not authorized to get the task total
        500:
            description: Error retrieving the task total
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin') and (current_user != user and user == 'automatic'):
        return {'msg': _('You don\'t have the required authorization')}, 401

    resp = services.get_tasks_total(user)

    return jsonify(resp), 200

@bp.route('', methods=['GET'])
@jwt_required()
def test_celery_result_all():
    """
    Returns the tasks currently running
    ---
    security:
        - JWT: []
    tags:
        - Processing Tasks
    responses:
        200:
            description: List of tasks
        401:
            description: Not authorized to get the running tasks
        500:
            description: Error retrieving the tasks
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Llamar al servicio para probar las tasks de celery
    i = app.celery_app.control.inspect()

    # Inspeccionar las tasks activas en los workers
    active = i.active()

    return active

# delete_task
@bp.route('/<taskId>', methods=['DELETE'])
@jwt_required()
def delete_task(taskId):
    """
    Deletes a task
    ---
    security:
        - JWT: []
    tags:
        - Processing Tasks
    parameters:
        - in: path
          name: taskId
          required: true
          type: string
          description: Task ID
    responses:
        200:
            description: The task was stopped successfully
        400:
            description: The task cannot be stopped
        401:
            description: Not authorized to delete the task
        404:
            description: The task does not exist
        500:
            description: Error deleting the task
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401

    return services.stop_task(taskId, current_user)