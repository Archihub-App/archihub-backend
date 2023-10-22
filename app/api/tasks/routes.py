from app.api.tasks import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.tasks import services
from app.api.users import services as user_services
from flask import request
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log

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
    responses:
        200:
            description: Lista de tasks
        500:
            description: Error al obtener las tasks
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    body = request.json
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin') or current_user != user:
        return {'msg': 'No tiene permisos para obtener las tasks'}, 401

    return services.get_tasks(user, body)

@bp.route('/total/<user>', methods=['GET'])
@jwt_required()
def get_tasks_total(user):
    """
    Obtener el total de tasks de un usuario
    ---
    tags:
        - Tareas de procesamiento
    responses:
        200:
            description: Total de tasks
        500:
            description: Error al obtener el total de tasks
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin') or current_user != user:
        return {'msg': 'No tiene permisos para obtener las tasks'}, 401

    return services.get_tasks_total(user)

@bp.route('', methods=['GET'])
# @jwt_required()
def test_celery_result_all():
    """
    Devuelve las tasks actualmente en ejecución
    ---
    tags:
        - Tareas de procesamiento
    responses:
        200:
            description: Listado de tasks
        500:
            description: Error al recuperar las tasks
    """
    # Obtener el usuario actual
    # current_user = get_jwt_identity()
    # # Verificar si el usuario tiene el rol de administrador
    # if not user_services.has_role(current_user, 'admin'):
    #     return {'msg': 'No tiene permisos para obtener las tasks'}, 401
    # Llamar al servicio para probar las tasks de celery
    i = app.celery_app.control.inspect()

    # Inspeccionar las tasks activas en los workers
    active = i.active()

    return active
