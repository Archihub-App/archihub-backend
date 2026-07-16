from app.api.usertasks import bp
from flask import jsonify
from flask import request
from . import services
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.users import services as user_services
from flask_babel import _

@bp.route('/tasks', methods=['POST'])
@jwt_required()
def get_tasks():
    """
    Obtener un listado paginado de tareas de usuario, filtradas por estado y opcionalmente por usuario
    ---
    security:
        - JWT: []
    tags:
        - Tareas
    description: >
        `user` debe estar SIEMPRE presente en el body (aunque sea vacío/null) — se accede
        como `body['user']` sin valor por defecto, así que su ausencia produce un KeyError
        no capturado (error 500 genérico de Flask), no un 400. Un usuario sin rol admin/
        team_lead solo puede consultar sus propias tareas (`user` debe igualar al usuario
        autenticado).
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                user:
                    type: string
                    description: Username a filtrar, o "" para "todos" (solo permitido con rol admin/team_lead)
                status:
                    type: array
                    items:
                        type: string
                    description: Estados a incluir (usertasks.status $in), p.ej. ["pending", "review"]
                page:
                    type: integer
                    default: 1
            required:
                - user
                - status
    responses:
        200:
            description: Tareas obtenidas exitosamente ({results, total})
        400:
            description: Debe especificar el estado de las tareas ("status" ausente en el body)
        401:
            description: No tiene permisos suficientes (rol insuficiente, o intenta ver tareas de otro usuario)
        500:
            description: Error al obtener las tareas (incluye el caso de "user" ausente en el body)
    """
    current_user = get_jwt_identity()
    body = request.json
    
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead') and not body['user']:
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
    
    if body['user'] != current_user and not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead'):
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
    
    if 'status' not in body:
        return jsonify({'msg': _('You must specify the status of the tasks')}), 400
    
    params ={
        'status': body['status'],
        'user': body['user'] if 'user' in body else None,
        'page': body['page'] if 'page' in body else 1,
    }
    
    return services.get_all_tasks(params)

@bp.route('/<resourceId>', methods=['GET'])
@jwt_required()
def get_resource_tasks(resourceId):
    """
    Obtener la tarea pendiente/en revisión/rechazada más reciente de un recurso
    ---
    security:
        - JWT: []
    tags:
        - Tareas
    description: Requiere el rol admin, team_lead o editor.
    parameters:
        - in: path
          name: resourceId
          type: string
          required: true
    responses:
        200:
            description: Tarea del recurso obtenida exitosamente
        401:
            description: No tiene permisos suficientes
        404:
            description: No hay tareas pendientes/en revisión/rechazadas para este recurso
        500:
            description: Error al obtener las tareas del recurso
    """
    current_user = get_jwt_identity()
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
    
    return services.get_resource_tasks(resourceId)

@bp.route('/record/<recordId>', methods=['GET'])
@jwt_required()
def get_record_tasks(recordId):
    """
    Obtener la tarea pendiente/en revisión/rechazada más reciente de un record
    ---
    security:
        - JWT: []
    tags:
        - Tareas
    description: Requiere el rol admin, team_lead, editor o transcriber.
    parameters:
        - in: path
          name: recordId
          type: string
          required: true
    responses:
        200:
            description: Tarea del record obtenida exitosamente
        401:
            description: No tiene permisos suficientes
        404:
            description: No hay tareas pendientes/en revisión/rechazadas para este record
        500:
            description: Error al obtener las tareas del record
    """
    current_user = get_jwt_identity()
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead') and not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'transcriber'):
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
    
    return services.get_record_tasks(recordId)

@bp.route('/editors', methods=['GET'])
@jwt_required()
def get_editors():
    """
    Obtener los usuarios con rol "editor" o "transcriber" (para asignarlos como editor de una tarea)
    ---
    security:
        - JWT: []
    tags:
        - Tareas
    description: Requiere el rol admin, team_lead, editor o transcriber.
    responses:
        200:
            description: Editores de tareas obtenidos exitosamente
        401:
            description: No tiene permisos suficientes
        500:
            description: Error al obtener los editores de tareas
    """
    current_user = get_jwt_identity()
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead') and not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'transcriber'):
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
    
    return services.get_editors()

@bp.route('', methods=['POST'])
@jwt_required()
def create_task():
    """
    Crear una tarea de revisión/transcripción sobre un recurso o un record
    ---
    security:
        - JWT: []
    tags:
        - Tareas
    description: >
        Requiere el rol admin o team_lead. Debe venir exactamente `resourceId` O `recordId`
        (no ambos son necesarios, pero se comprueba `resourceId` primero); no puede existir
        ya una tarea con status "pending" para ese mismo recurso/record. El status inicial
        siempre se fija a "pending" y el comentario inicial queda asociado al usuario autenticado.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                resourceId:
                    type: string
                    description: Id del recurso a revisar (mutuamente alternativo con recordId)
                recordId:
                    type: string
                    description: Id del record/archivo a transcribir (mutuamente alternativo con resourceId)
                user:
                    type: string
                    description: Username del usuario asignado a la tarea
                comment:
                    type: string
                    description: Comentario/instrucción inicial de la tarea
            required:
                - user
                - comment
    responses:
        201:
            description: Tarea creada exitosamente
        400:
            description: >
                Falta resourceId/recordId, falta user, falta comment, alguno viene vacío,
                o ya existe una tarea pendiente para ese recurso/record
        401:
            description: No tiene el rol admin/team_lead requerido
        500:
            description: Error al crear la tarea
    """
    current_user = get_jwt_identity()
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'team_lead'):
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
    
    return services.create_task(request.json, current_user)

@bp.route('/<taskId>', methods=['PUT'])
@jwt_required()
def update_task(taskId):
    """
    Actualizar una tarea (agregar comentario y, opcionalmente, transicionar su estado)
    ---
    security:
        - JWT: []
    tags:
        - Tareas
    description: >
        Requiere el rol editor, team_lead, transcriber o admin. `comment` es obligatorio en
        cada actualización (se accede como `body['comment']` sin valor por defecto; su
        ausencia produce un KeyError no capturado -> error 500, no un 400) y se añade al
        historial de comentarios existente. Transiciones de estado permitidas: una tarea
        "pending" solo puede pasar a "review", y solo por el propio usuario asignado; una
        tarea "review" solo puede pasar a "approved"/"rejected", y solo por un team_lead o
        admin. Una tarea ya "approved" no puede modificarse.
    parameters:
        - in: path
          name: taskId
          type: string
          required: true
        - in: body
          name: body
          schema:
            type: object
            properties:
                comment:
                    type: string
                status:
                    type: string
                    enum: [review, approved, rejected]
                user:
                    type: string
            required:
                - comment
    responses:
        200:
            description: Tarea actualizada exitosamente
        400:
            description: La tarea ya está "approved" y no admite más cambios
        401:
            description: No tiene el rol requerido para la transición de estado solicitada
        404:
            description: La tarea no existe
        500:
            description: Error al actualizar la tarea (incluye el caso de "comment" ausente en el body)
    """
    current_user = get_jwt_identity()
    if not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'team_lead') and not user_services.has_role(current_user, 'transcriber') and not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
    
    return services.update_task(taskId, request.json, current_user, user_services.has_role(current_user, 'team_lead'))