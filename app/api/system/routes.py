from app.api.system import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.system import services
from app.api.users import services as user_services
from flask import request
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log

from app.tasks.tasks import add
from celery.result import AsyncResult

from flask import current_app as app

# En este archivo se registran las rutas de la API para los ajustes del sistema

# GET para obtener todos los ajustes del sistema
@bp.route('', methods=['GET'])
@jwt_required()
def get_all():
    """
    Obtener todos los ajustes del sistema
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Lista de ajustes del sistema
        401:
            description: No tiene permisos para obtener los ajustes del sistema
        500:
            description: Error al obtener los ajustes del sistema
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': 'No tiene permisos para obtener los ajustes del sistema'}, 401
    # Llamar al servicio para obtener todos los ajustes del sistema
    return services.get_all_settings()

# PUT para actualizar los ajustes del sistema
@bp.route('', methods=['PUT'])
@jwt_required()
def update():
    """
    Actualizar los ajustes del sistema
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Ajustes del sistema actualizados exitosamente
        401:
            description: No tiene permisos para actualizar los ajustes del sistema
        500:
            description: Error al actualizar los ajustes del sistema
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': 'No tiene permisos para actualizar los ajustes del sistema'}, 401
    # Obtener el body de la request
    body = request.get_json()

    print(body)

    # Llamar al servicio para actualizar los ajustes del sistema
    return services.update_settings(body, current_user)

# GET para obtener el tipo por defecto del modulo de catalogacion
@bp.route('/default-cataloging-type', methods=['GET'])
@jwt_required()
def get_default_cataloging_type():
    """
    Obtener el tipo por defecto del modulo de catalogacion
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Tipo por defecto del modulo de catalogacion
        404:
            description: No existe el tipo por defecto del modulo de catalogacion
        500:
            description: Error al obtener el tipo por defecto del modulo de catalogacion
    """
    # Llamar al servicio para obtener el tipo por defecto del modulo de catalogacion
    return services.get_default_cataloging_type()

# GET para obtener el listado de plugins en la carpeta plugins
@bp.route('/plugins', methods=['GET'])
@jwt_required()
def get_plugins():
    """
    Obtener el listado de plugins en la carpeta plugins
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Listado de plugins en la carpeta plugins
        401:
            description: No tiene permisos para obtener el listado de plugins en la carpeta plugins
        500:
            description: Error al obtener el listado de plugins en la carpeta plugins
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': 'No tiene permisos para obtener el listado de plugins en la carpeta plugins'}, 401
    # Llamar al servicio para obtener el listado de plugins en la carpeta plugins
    return services.get_plugins()

# POST para instalar un plugin
@bp.route('/plugins', methods=['POST'])
@jwt_required()
def activate_plugin():
    """
    Activar plugins
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Plugins activados exitosamente
        401:
            description: No tiene permisos para activar los plugins
        500:
            description: Error al activar los plugins
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': 'No tiene permisos para activar los plugins'}, 401
    # Obtener el body de la request
    body = request.get_json()
    # Llamar al servicio para instalar un plugin
    return services.activate_plugin(body, current_user)

# GET para obtener el listado de access rights
@bp.route('/access-rights', methods=['GET'])
@jwt_required()
def get_access_rights():
    """
    Obtener el listado de access rights
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Listado de access rights
        401:
            description: No tiene permisos para obtener el listado de access rights
        500:
            description: Error al obtener el listado de access rights
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': 'No tiene permisos para obtener el listado de access rights'}, 401
    # Llamar al servicio para obtener el listado de access rights
    return services.get_access_rights()

# GET para obterner el listado de roles
@bp.route('/roles', methods=['GET'])
@jwt_required()
def get_roles():
    """
    Obtener el listado de roles
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Listado de roles
        401:
            description: No tiene permisos para obtener el listado de roles
        500:
            description: Error al obtener el listado de roles
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': 'No tiene permisos para obtener el listado de roles'}, 401
    # Llamar al servicio para obtener el listado de roles
    return services.get_roles()


# GET para probar las tasks de celery
@bp.route('/test-celery', methods=['GET'])

def test_celery():
    """
    Probar las tasks de celery
    ---
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Task ejecutada exitosamente
        500:
            description: Error al ejecutar la task
    """
    # Llamar al servicio para probar las tasks de celery
    task = add.delay("d9f7860cd4b6")
    print(task.id)

    return "ok"


@bp.route('/test-celery-result', methods=['GET'])

def test_celery_result_all():
    """
    Probar las tasks de celery
    ---
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Task ejecutada exitosamente
        500:
            description: Error al ejecutar la task
    """
    # Llamar al servicio para probar las tasks de celery
    i = app.celery_app.control.inspect()

    # Inspeccionar las tasks activas en los workers
    active = i.active()

    return 'ok'

@bp.route('/test-celery-result/<id>', methods=['GET'])

def test_celery_result(id):
    """
    Probar las tasks de celery
    ---
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Task ejecutada exitosamente
        500:
            description: Error al ejecutar la task
    """
    # Llamar al servicio para probar las tasks de celery
    result = AsyncResult(id)
    return {
        "ready": result.ready(),
        "successful": result.successful(),
        "value": result.result if result.ready() else None,
    }