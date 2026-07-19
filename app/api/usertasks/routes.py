from app.api.usertasks import bp
from flask import jsonify
from flask import request
from . import services
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.users import services as user_services
from flask_babel import _

@bp.route('/tasks', methods=['POST'])
@jwt_required()
def get_tasks():
    """
    Get a paginated list of user tasks, filtered by status and optionally by user
    ---
    security:
        - JWT: []
    tags:
        - Tasks
    description: >
        `user` must ALWAYS be present in the body (even if empty/null) — it's accessed
        as `body['user']` with no default value, so its absence produces an uncaught
        KeyError (generic Flask 500 error), not a 400. A user without the admin/
        team_lead role can only view their own tasks (`user` must match the
        authenticated user).
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                user:
                    type: string
                    description: Username to filter by, or "" for "all" (only allowed with the admin/team_lead role)
                status:
                    type: array
                    items:
                        type: string
                    description: Statuses to include (usertasks.status $in), e.g. ["pending", "review"]
                page:
                    type: integer
                    default: 1
            required:
                - user
                - status
    responses:
        200:
            description: Tasks retrieved successfully ({results, total})
        400:
            description: Task status must be specified ("status" missing from the body)
        401:
            description: Not authorized (insufficient role, or attempting to view another user's tasks)
        500:
            description: Error retrieving the tasks (includes the case of a missing "user" in the body)
    """
    current_user = get_jwt_identity()
    body = request.json
    
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead') and not body['user']:
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
    
    if body['user'] != current_user and not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead'):
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
    
    if 'status' not in body:
        return jsonify({'msg': _('You must specify the status of the tasks')}), 400
    
    params ={
        'status': body['status'],
        'user': body['user'] if 'user' in body else None,
        'page': body['page'] if 'page' in body else 1,
    }
    
    return services.get_all_tasks(params)

@bp.route('/<resourceId>', methods=['GET'])
@jwt_required()
def get_resource_tasks(resourceId):
    """
    Get the most recent pending/in-review/rejected task for a resource
    ---
    security:
        - JWT: []
    tags:
        - Tasks
    description: Requires the admin, team_lead, or editor role.
    parameters:
        - in: path
          name: resourceId
          type: string
          required: true
    responses:
        200:
            description: Resource task retrieved successfully
        401:
            description: Not authorized
        404:
            description: No pending/in-review/rejected tasks for this resource
        500:
            description: Error retrieving the resource's tasks
    """
    current_user = get_jwt_identity()
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
    
    return services.get_resource_tasks(resourceId)

@bp.route('/record/<recordId>', methods=['GET'])
@jwt_required()
def get_record_tasks(recordId):
    """
    Get the most recent pending/in-review/rejected task for a record
    ---
    security:
        - JWT: []
    tags:
        - Tasks
    description: Requires the admin, team_lead, editor, or transcriber role.
    parameters:
        - in: path
          name: recordId
          type: string
          required: true
    responses:
        200:
            description: Record task retrieved successfully
        401:
            description: Not authorized
        404:
            description: No pending/in-review/rejected tasks for this record
        500:
            description: Error retrieving the record's tasks
    """
    current_user = get_jwt_identity()
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead') and not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'transcriber'):
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
    
    return services.get_record_tasks(recordId)

@bp.route('/editors', methods=['GET'])
@jwt_required()
def get_editors():
    """
    Get users with the "editor" or "transcriber" role (to assign them as a task's editor)
    ---
    security:
        - JWT: []
    tags:
        - Tasks
    description: Requires the admin, team_lead, editor, or transcriber role.
    responses:
        200:
            description: Task editors retrieved successfully
        401:
            description: Not authorized
        500:
            description: Error retrieving the task editors
    """
    current_user = get_jwt_identity()
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead') and not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'transcriber'):
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
    
    return services.get_editors()

@bp.route('', methods=['POST'])
@jwt_required()
def create_task():
    """
    Create a review/transcription task on a resource or a record
    ---
    security:
        - JWT: []
    tags:
        - Tasks
    description: >
        Requires the admin or team_lead role. Exactly `resourceId` OR `recordId` must be
        given (both aren't needed, but `resourceId` is checked first); a "pending" task
        must not already exist for that same resource/record. The initial status is
        always set to "pending" and the initial comment is associated with the authenticated user.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                resourceId:
                    type: string
                    description: Id of the resource to review (mutually exclusive with recordId)
                recordId:
                    type: string
                    description: Id of the record/file to transcribe (mutually exclusive with resourceId)
                user:
                    type: string
                    description: Username of the user assigned to the task
                comment:
                    type: string
                    description: Initial comment/instruction for the task
            required:
                - user
                - comment
    responses:
        201:
            description: Task created successfully
        400:
            description: >
                Missing resourceId/recordId, missing user, missing comment, one of them is
                empty, or a pending task already exists for that resource/record
        401:
            description: Missing the required admin/team_lead role
        500:
            description: Error creating the task
    """
    current_user = get_jwt_identity()
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead'):
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
    
    return services.create_task(request.json, current_user)

@bp.route('/<taskId>', methods=['PUT'])
@jwt_required()
def update_task(taskId):
    """
    Update a task (add a comment and, optionally, transition its status)
    ---
    security:
        - JWT: []
    tags:
        - Tasks
    description: >
        Requires the editor, team_lead, transcriber, or admin role. `comment` is required
        on every update (accessed as `body['comment']` with no default value; its absence
        produces an uncaught KeyError -> 500 error, not a 400) and is appended to the
        existing comment history. Allowed status transitions: a "pending" task can only
        move to "review", and only by the assigned user; a "review" task can only move
        to "approved"/"rejected", and only by a team_lead or admin. An already-"approved"
        task can no longer be modified.
    parameters:
        - in: path
          name: taskId
          type: string
          required: true
        - in: body
          name: body
          schema:
            type: object
            properties:
                comment:
                    type: string
                status:
                    type: string
                    enum: [review, approved, rejected]
                user:
                    type: string
            required:
                - comment
    responses:
        200:
            description: Task updated successfully
        400:
            description: The task is already "approved" and no longer accepts changes
        401:
            description: Missing the role required for the requested status transition
        404:
            description: The task does not exist
        500:
            description: Error updating the task (includes the case of a missing "comment" in the body)
    """
    current_user = get_jwt_identity()
    if not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'team_lead') and not user_services.has_role(current_user, 'transcriber') and not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
    
    return services.update_task(taskId, request.json, current_user, user_services.has_role(current_user, 'team_lead'))