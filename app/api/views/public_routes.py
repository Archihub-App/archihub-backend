from app.api.views import bp
from flask import jsonify
from flask import request
from app.api.views import services

#Nuevo GET endpoint para obtener todas las vistas de consulta
@bp.route('', methods=['GET'])
def get_views():
    """
    Obtener el listado público de todas las vistas de consulta (nombre, slug, descripción, thumbnail)
    ---
    tags:
        - Vistas
    description: Ruta pública, no requiere autenticación.
    responses:
        200:
            description: Retorna todas las vistas de consulta
        500:
            description: Error al obtener las vistas de consulta
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
    Obtener información extendida de una vista de consulta por su slug (formularios, tipos,
    conteo de archivos por tipo, etc.), usada para renderizar la vista pública
    ---
    tags:
        - Vistas
    description: Ruta pública, no requiere autenticación.
    parameters:
        - in: path
          name: view_slug
          type: string
          required: true
    responses:
        200:
            description: Información de la vista de consulta
        500:
            description: >
                Error interno no manejado. Nota: el código intenta iterar view['visible']
                antes de comprobar si la vista existe, por lo que un slug inexistente produce
                una excepción sin capturar (TypeError, página de error 500 genérica de Flask),
                no la respuesta 404 documentada en versiones anteriores de este endpoint.
    """
    # Llamar al servicio para obtener la información de una vista de consulta
    resp = services.get_view_info(view_slug)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp