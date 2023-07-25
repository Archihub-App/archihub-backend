from app.api.postTypes import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.postTypes import services

# En este archivo se registran las rutas de la API para los tipos de post

# Nuevo endpoint para obtener todos los tipos de post
@bp.route('', methods=['GET'])
@jwt_required()
def get_all():
    """
    Obtener todos los tipos de post
    ---
    security:
        - JWT: []
    tags:
        - Tipos de post
    responses:
        200:
            description: Lista de tipos de post
    """
    # Llamar al servicio para obtener todos los tipos de post
    return services.get_all()
