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
    Obtener las tasks de un usuario
    ---
    tags:
        - Tareas de procesamiento
    parameters:
        - in: path
          name: user
          required: true
          type: string
          description: Nombre de usuario
        - in: body
          name: body
          required: true
          schema:
            properties:
                page:
                    type: integer
                    description: Página de resultados
    responses:
        200:
            description: Lista de tasks
        401:
            description: No tiene permisos para obtener las tareas
        500:
            description: Error al obtener las tasks
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
    Obtener el total de tasks de un usuario
    ---
    tags:
        - Tareas de procesamiento
    parameters:
        - in: path
          name: user
          required: true
          type: string
          description: Nombre de usuario
    responses:
        200:
            description: Total de tasks
        401:
            description: No tiene permisos para obtener el total de tareas
        500:
            description: Error al obtener el total de tasks
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
    Devuelve las tasks actualmente en ejecución
    ---
    tags:
        - Tareas de procesamiento
    responses:
        200:
            description: Listado de tasks
        401:
            description: No tiene permisos para obtener las tareas en ejecución
        500:
            description: Error al recuperar las tasks
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
    Elimina una task
    ---
    tags:
        - Tareas de procesamiento
    parameters:
        - in: path
          name: taskId
          required: true
          type: string
          description: ID de la tarea
    responses:
        200:
            description: La tarea se detuvo correctamente
        400:
            description: No se puede detener la tarea
        401:
            description: No tiene permisos para eliminar la tarea
        404:
            description: La tarea no existe
        500:
            description: Error al eliminar la task
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401

    return services.stop_task(taskId, current_user)