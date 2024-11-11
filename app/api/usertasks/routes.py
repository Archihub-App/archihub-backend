from app.api.usertasks import bp
from flask import jsonify
from flask import request
from . import services
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.users import services as user_services

@bp.route('/<resourceId>', methods=['GET'])
@jwt_required()
def get_resource_tasks(resourceId):
    """
    Obtener las tareas de un recurso
    ---
    tags:
        - Tareas
    parameters:
        - in: path
          name: id
          schema:
            type: string
          required: true
    responses:
        200:
            description: Tareas del recurso obtenidas exitosamente
        500:
            description: Error al obtener las tareas del recurso
    """
    current_user = get_jwt_identity()
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead'):
        return jsonify({'msg': 'No tiene permisos suficientes'}), 401
    
    return services.get_resource_tasks(resourceId)

@bp.route('/editors', methods=['GET'])
@jwt_required()
def get_editors():
    """
    Obtener los editores de tareas
    ---
    tags:
        - Tareas
    responses:
        200:
            description: Editores de tareas obtenidos exitosamente
        500:
            description: Error al obtener los editores de tareas
    """
    current_user = get_jwt_identity()
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead'):
        return jsonify({'msg': 'No tiene permisos suficientes'}), 401
    
    return services.get_editors()

@bp.route('', methods=['POST'])
@jwt_required()
def create_task():
    """
    Crear una tarea
    ---
    tags:
        - Tareas
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                resourceId:
                    type: string
                task:
                    type: string
                editor:
                    type: string
    responses:
        200:
            description: Tarea creada exitosamente
        500:
            description: Error al crear la tarea
    """
    current_user = get_jwt_identity()
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead'):
        return jsonify({'msg': 'No tiene permisos suficientes'}), 401
    
    return services.create_task(request.json, current_user)

@bp.route('/<taskId>', methods=['PUT'])
@jwt_required()
def update_task(taskId):
    """
    Actualizar una tarea
    ---
    tags:
        - Tareas
    parameters:
        - in: path
          name: id
          schema:
            type: string
          required: true
        - in: body
          name: body
          schema:
            type: object
            properties:
                resourceId:
                    type: string
                task:
                    type: string
                editor:
                    type: string
                status:
                    type: string
    responses:
        200:
            description: Tarea actualizada exitosamente
        500:
            description: Error al actualizar la tarea
    """
    current_user = get_jwt_identity()
    if not user_services.has_role(current_user, 'editor') or not user_services.has_role(current_user, 'team_lead'):
        return jsonify({'msg': 'No tiene permisos suficientes'}), 401
    
    return services.update_task(taskId, request.json, current_user, user_services.has_role(current_user, 'team_lead'))