from app.api.health import bp
from app.api.health import services
from app.api.health import testcontrol_services
from app.utils.TestControlAuth import testControlAuthenticate


@bp.route('/live', methods=['GET'])
def live():
    """
    Confirma que el proceso de Flask está en ejecución
    ---
    tags:
        - Salud del sistema
    responses:
        200:
            description: El proceso está vivo
    """
    return {'alive': True}, 200


@bp.route('/ready', methods=['GET'])
def ready():
    """
    Verifica la conectividad con MongoDB, Redis, Elasticsearch, Qdrant y Celery
    ---
    tags:
        - Salud del sistema
    responses:
        200:
            description: Todas las dependencias requeridas están disponibles
        503:
            description: Al menos una dependencia no está disponible
    """
    resp, status = services.get_readiness()
    return resp, status


# --- Test-control (only functional when ARCHIHUB_TEST_MODE=true and the
# Mongo `system` marker is present — see app/utils/TestControlAuth.py) ---

@bp.route('/test-control/status', methods=['GET'])
@testControlAuthenticate
def test_control_status():
    """
    Devuelve metadatos de la instancia de pruebas desechable
    ---
    tags:
        - Control de pruebas
    parameters:
        - in: header
          name: X-ArchiHUB-Test-Secret
          required: true
          type: string
    responses:
        200:
            description: Metadatos de la instancia
        401:
            description: Secreto de pruebas inválido o ausente
        403:
            description: La instancia no está marcada como desechable
        404:
            description: El modo de pruebas no está activo
    """
    resp, status_code = testcontrol_services.get_status()
    return resp, status_code


@bp.route('/test-control/routes', methods=['GET'])
@testControlAuthenticate
def test_control_routes():
    """
    Devuelve el inventario en vivo de rutas de Flask, para compararlo con Swagger
    ---
    tags:
        - Control de pruebas
    parameters:
        - in: header
          name: X-ArchiHUB-Test-Secret
          required: true
          type: string
    responses:
        200:
            description: Inventario de rutas
        401:
            description: Secreto de pruebas inválido o ausente
        403:
            description: La instancia no está marcada como desechable
        404:
            description: El modo de pruebas no está activo
    """
    resp, status_code = testcontrol_services.get_routes()
    return resp, status_code


@bp.route('/test-control/reset', methods=['POST'])
@testControlAuthenticate
def test_control_reset():
    """
    Reinicia la instancia desechable: borra los datos y siembra una línea base determinística
    ---
    tags:
        - Control de pruebas
    parameters:
        - in: header
          name: X-ArchiHUB-Test-Secret
          required: true
          type: string
    responses:
        202:
            description: El reinicio se encoló; sondear /health/test-control/reset/{taskId}
        401:
            description: Secreto de pruebas inválido o ausente
        403:
            description: La instancia no está marcada como desechable
        404:
            description: El modo de pruebas no está activo
    """
    resp, status_code = testcontrol_services.start_reset()
    return resp, status_code


@bp.route('/test-control/reset/<task_id>', methods=['GET'])
@testControlAuthenticate
def test_control_reset_status(task_id):
    """
    Sondea el estado de una tarea de reinicio y, una vez completada, devuelve las
    credenciales de administrador generadas para esta ejecución
    ---
    tags:
        - Control de pruebas
    parameters:
        - in: header
          name: X-ArchiHUB-Test-Secret
          required: true
          type: string
        - in: path
          name: task_id
          required: true
          type: string
    responses:
        200:
            description: Estado de la tarea de reinicio
        401:
            description: Secreto de pruebas inválido o ausente
        403:
            description: La instancia no está marcada como desechable
        404:
            description: El modo de pruebas no está activo
    """
    resp, status_code = testcontrol_services.poll_reset(task_id)
    return resp, status_code
