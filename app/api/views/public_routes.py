from app.api.views import bp
from flask import jsonify
from flask import request
from app.api.views import services

#Nuevo GET endpoint para obtener todas las vistas de consulta
@bp.route('', methods=['GET'])
def get_views():
    """
    Get the public listing of all query views (name, slug, description, thumbnail)
    ---
    tags:
        - Views
    description: Public route, does not require authentication.
    responses:
        200:
            description: Returns all query views
        500:
            description: Error retrieving the query views
    """
    # Llamar al servicio para obtener todas las vistas de consulta
    resp = services.get_all()
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/info/<view_slug>', methods=['GET'])
def get_view_info(view_slug):
    """
    Get extended information for a query view by its slug (forms, types,
    file count per type, etc.), used to render the public view
    ---
    tags:
        - Views
    description: Public route, does not require authentication.
    parameters:
        - in: path
          name: view_slug
          type: string
          required: true
    responses:
        200:
            description: Query view information
        500:
            description: >
                Unhandled internal error. Note: the code attempts to iterate view['visible']
                before checking whether the view exists, so a nonexistent slug produces
                an uncaught exception (TypeError, Flask's generic 500 error page),
                not the 404 response documented in earlier versions of this endpoint.
    """
    # Llamar al servicio para obtener la información de una vista de consulta
    resp = services.get_view_info(view_slug)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp