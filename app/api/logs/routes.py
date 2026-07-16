from app.api.logs import bp
from flask import jsonify
from flask import request
from . import services
from app.api.users import services as user_services
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from flask_babel import _
from app.utils.FernetAuth import fernetAuthenticate
# En este archivo se registran las rutas de la API para los logs


@bp.route('/actions', methods=['GET'])
@jwt_required()
def get_log_actions():
    """
    Obtener el catálogo de acciones de log disponibles (LogActions.log_actions)
    ---
    security:
        - JWT: []
    tags:
        - Logs del sistema
    responses:
        200:
            description: Diccionario de acciones de log disponibles (siempre 200, no depende de la base de datos)
        401:
            description: No tienes permisos para realizar esta acción (el usuario autenticado no tiene el rol admin)
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para obtener las acciones de log
    return services.get_log_actions()

# Nuevo POST endpoint para obtener los logs de acuerdo a un filtro
@bp.route('', methods=['POST'])
@jwt_required()
def filter():
    """
    Obtener los logs de acuerdo a un filtro (paginado)
    ---
    security:
        - JWT: []
    tags:
        - Logs del sistema
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
              filters:
                type: object
                description: 'Filtro de Mongo aplicado directamente sobre la colección "logs" (p. ej. username, action).'
              page:
                type: integer
                description: Página de resultados (20 por página, skip = page * 20).
            required:
              - filters
              - page
    responses:
        200:
            description: >
                Arreglo de logs (con "details" derivado de metadata y "total" agregado en cada elemento).
                Nota: por un bug conocido en el servicio (filter() evalúa "if not logs" sobre un cursor de
                pymongo, que siempre es verdadero), la ausencia de resultados NUNCA produce 404: siempre
                responde 200, con un arreglo vacío si no hay coincidencias.
        401:
            description: No tienes permisos para realizar esta acción (el usuario autenticado no tiene el rol admin)
        500:
            description: Error obteniendo logs (incluye el caso en que "filters" o "page" no vienen en el body)
    """
    # Obtener el body del request
    body = request.json
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para obtener los logs de acuerdo a un filtro
    return services.filter(body)

# Nuevo GET endpoint para obtener todos los logs de un recurso
@bp.route('/resource/<resource_id>', methods=['POST'])
@jwt_required()
def get_logs(resource_id):
    """
    Obtener el historial de cambios (diffs) de un recurso, a partir de sus logs RESOURCE_CREATE/RESOURCE_UPDATE
    ---
    security:
        - JWT: []
    tags:
        - Logs del sistema
    parameters:
        - in: path
          name: resource_id
          required: true
          type: string
          description: ID del recurso (metadata.resource._id en los logs)
        - in: body
          name: body
          schema:
                type: object
                properties:
                    page:
                        type: integer
                        description: Página de resultados (20 por página, skip = page * 20). Opcional, por defecto skip=0.
    responses:
        200:
            description: >
                Arreglo de cambios detectados (path/date/old/new) comparando pares consecutivos de logs
                RESOURCE_CREATE/RESOURCE_UPDATE del recurso. Vacío si hay menos de dos logs para comparar.
                Nota: por el mismo bug de "if not logs" sobre un cursor de pymongo descrito en POST /logs,
                la ausencia de logs para el recurso NUNCA produce 404: siempre responde 200 (con [] si no hay
                logs, o si no alcanza a haber al menos dos para comparar cambios).
        401:
            description: No tienes permisos para realizar esta acción (el usuario autenticado no tiene el rol admin)
        500:
            description: Error obteniendo logs
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    
    # Obtener el body del request
    body = request.json

    # Llamar al servicio para obtener los logs de un recurso
    return services.get_logs(body, resource_id)
