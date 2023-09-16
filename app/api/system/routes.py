from app.api.system import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.system import services
from app.api.users import services as user_services
from flask import request
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log


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
        return {'msg': 'No tiene permisos para crear un tipo de contenido'}, 401
    # Llamar al servicio para obtener todos los ajustes del sistema
    return services.get_all_settings()