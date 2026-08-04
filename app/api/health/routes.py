from app.api.health import bp
from app.api.health import services
from app.api.health import testcontrol_services
from app.utils.TestControlAuth import testControlAuthenticate


@bp.route('/live', methods=['GET'])
def live():
    """
    Confirms that the Flask process is running
    ---
    tags:
        - System health
    responses:
        200:
            description: The process is alive
    """
    return {'alive': True}, 200


@bp.route('/ready', methods=['GET'])
def ready():
    """
    Checks connectivity with MongoDB, Redis, Elasticsearch, Qdrant, and Celery
    ---
    tags:
        - System health
    responses:
        200:
            description: All required dependencies are available
        503:
            description: At least one dependency is unavailable
    """
    resp, status = services.get_readiness()
    return resp, status


# --- Test-control (only functional when ARCHIHUB_TEST_MODE=true and the
# Mongo `system` marker is present — see app/utils/TestControlAuth.py) ---

@bp.route('/test-control/status', methods=['GET'])
@testControlAuthenticate
def test_control_status():
    """
    Returns metadata about the disposable test instance
    ---
    tags:
        - Test control
    parameters:
        - in: header
          name: X-ArchiHUB-Test-Secret
          required: true
          type: string
    responses:
        200:
            description: Instance metadata
        401:
            description: Invalid or missing test secret
        403:
            description: The instance is not marked as disposable
        404:
            description: Test mode is not active
    """
    resp, status_code = testcontrol_services.get_status()
    return resp, status_code


@bp.route('/test-control/routes', methods=['GET'])
@testControlAuthenticate
def test_control_routes():
    """
    Returns the live inventory of Flask routes, to compare against Swagger
    ---
    tags:
        - Test control
    parameters:
        - in: header
          name: X-ArchiHUB-Test-Secret
          required: true
          type: string
    responses:
        200:
            description: Route inventory
        401:
            description: Invalid or missing test secret
        403:
            description: The instance is not marked as disposable
        404:
            description: Test mode is not active
    """
    resp, status_code = testcontrol_services.get_routes()
    return resp, status_code


@bp.route('/test-control/reset', methods=['POST'])
@testControlAuthenticate
def test_control_reset():
    """
    Resets the disposable instance: wipes the data and seeds a deterministic baseline
    ---
    tags:
        - Test control
    parameters:
        - in: header
          name: X-ArchiHUB-Test-Secret
          required: true
          type: string
    responses:
        202:
            description: The reset was queued; poll /health/test-control/reset/{taskId}
        401:
            description: Invalid or missing test secret
        403:
            description: The instance is not marked as disposable
        404:
            description: Test mode is not active
    """
    resp, status_code = testcontrol_services.start_reset()
    return resp, status_code


@bp.route('/test-control/reset/<task_id>', methods=['GET'])
@testControlAuthenticate
def test_control_reset_status(task_id):
    """
    Polls the status of a reset task and, once completed, returns the
    admin credentials generated for this run
    ---
    tags:
        - Test control
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
            description: Reset task status
        401:
            description: Invalid or missing test secret
        403:
            description: The instance is not marked as disposable
        404:
            description: Test mode is not active
    """
    resp, status_code = testcontrol_services.poll_reset(task_id)
    return resp, status_code
