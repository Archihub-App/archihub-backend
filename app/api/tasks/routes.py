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

# En este archivo se registran las rutas de la API para los ajustes del sistema



@bp.route('/tasks', methods=['GET'])
@jwt_required()
def test_celery_result_all():
    """
    Devuelve las tasks actualmente en ejecución
    ---
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Listado de tasks
        500:
            description: Error al recuperar las tasks
    """
    # Llamar al servicio para probar las tasks de celery
    i = app.celery_app.control.inspect()

    # Inspeccionar las tasks activas en los workers
    active = i.active()

    return 'ok'

@bp.route('/task-result/<id>', methods=['GET'])
@jwt_required()
def test_celery_result(id):
    """
    Devuelve el estado de una task
    ---
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Estado de la task
        500:
            description: Error al recuperar el estado de la task
    """
    # Llamar al servicio para probar las tasks de celery
    result = AsyncResult(id)
    return {
        "ready": result.ready(),
        "successful": result.successful(),
        "value": result.result if result.ready() else None,
    }