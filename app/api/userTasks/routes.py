from app.api.userTasks import bp
from flask import request
from flask_jwt_extended import get_jwt_identity
from flask_jwt_extended import jwt_required
from . import services
from app.api.users import services as user_services


@bp.route('', methods=['POST'])
@jwt_required()
def create_user_task():
    """
    Crea una nueva tarea para un usuario.
    """
    current_user = get_jwt_identity()
    body = request.json

    if not user_services.has_role(current_user, 'admin') or not user_services.has_role(current_user, 'team_lead'):
        return {'msg': 'No tienes permisos para realizar esta acción'}, 401

    return services.create_user_task(current_user, body)

@bp.route('', methods=['DELETE'])
@jwt_required()
def delete_user_task():
    """
    Elimina una tarea para un usuario.
    """
    current_user = get_jwt_identity()
    body = request.json

    if not user_services.has_role(current_user, 'admin') or not user_services.has_role(current_user, 'team_lead'):
        return {'msg': 'No tienes permisos para realizar esta acción'}, 401

    return services.delete_user_task(body)