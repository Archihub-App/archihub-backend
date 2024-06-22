from app.api.snaps import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from flask import request
from app.api.snaps import services

@bp.route('', methods=['POST'])
@jwt_required()
def create_snap():
    """
    Crear un nuevo recorte
    ---
    tags:
      - snaps
    responses:
        200:
            description: Recorte creado
        400:
            description: Error en la petición
        401:
            description: Token inválido
    """
    user = get_jwt_identity()
    body = request.json

    return services.create(user, body)

@bp.route('/<id>', methods=['DELETE'])
@jwt_required()
def delete_snap(id):
    """
    Eliminar un recorte por su id
    ---
    tags:
      - snaps
    parameters:
      - in: path
        name: id
        schema:
          type: string
        required: true
        description: Id del recorte
    responses:
        200:
            description: Recorte eliminado
        400:
            description: Error en la petición
        401:
            description: Token inválido
    """
    user = get_jwt_identity()

    return services.delete_by_id(id, user)

@bp.route('/<id>', methods=['GET'])
@jwt_required()
def get_snap(id):
    """
    Obtener un recorte por su id
    ---
    tags:
      - snaps
    parameters:
      - in: path
        name: id
        schema:
          type: string
        required: true
        description: Id del recorte
    responses:
        200:
            description: Recorte encontrado
        400:
            description: Error en la petición
        401:
            description: Token inválido
    """
    user = get_jwt_identity()

    return services.get_by_id(id, user)