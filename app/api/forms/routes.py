from app.api.forms import bp
from flask import jsonify, request
from flask_jwt_extended import jwt_required
from app.api.forms import services

# En este archivo se registran las rutas de la API para los estándares de metadatos

# Nuevo endpoint para obtener todos los estándares de metadatos
@bp.route('', methods=['GET'])
@jwt_required()
def get_all():
    """
    Obtener todos los estándares de metadatos
    ---
    security:
        - JWT: []
    tags:
        - Estándares de metadatos
    responses:
        200:
            description: Lista de estándares de metadatos
    """
    # Llamar al servicio para obtener todos los estándares de metadatos
    return services.get_all()