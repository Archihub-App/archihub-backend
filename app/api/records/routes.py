from app.api.records import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.records import services
from app.api.users import services as user_services
from flask import request, jsonify
import json

# En este archivo se registran las rutas de la API para los records

# Nuevo endpoint para obtener todos los records dado un body de filtros
@bp.route('', methods=['POST'])
@jwt_required()
def get_all():
    """
    Obtener todos los records dado un body de filtros
    ---
    security:
        - JWT: []
    tags:
        - Records
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                filters:
                    type: object
                sort:
                    type: string
                limit:
                    type: integer
                skip:
                    type: integer
    responses:
        200:
            description: Records obtenidos exitosamente
        401:
            description: No tiene permisos para obtener los records
        500:
            description: Error al obtener los records
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # si el usuario no es admin
    if not user_services.has_role(current_user, 'admin'):
        # retornar error
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Obtener el body del request
    body = request.json
    # Llamar al servicio para obtener los records
    return services.get_by_filters(body, current_user)

# Nuevo endpoint para obtener un record por su id
@bp.route('/<id>', methods=['GET'])
@jwt_required()
def get_by_id(id):
    """
    Obtener un record por su id
    ---
    security:
        - JWT: []
    tags:
        - Records
    parameters:
        - in: path
          name: id
          schema:
            type: string
            required: true
            description: id del record a obtener
    responses:
        200:
            description: Record
        401:
            description: No tiene permisos para obtener un record
        404:
            description: Record no existe
        500:
            description: Error al obtener el record
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    # Llamar al servicio para obtener un record por su id
    resp = services.get_by_id(id, current_user)
    
    return resp