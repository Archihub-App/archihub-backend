from app.api.snaps import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from flask import request
from app.api.snaps import services

@bp.route('', methods=['POST'])
@jwt_required()
def create_snap():
    """
    Crear un nuevo recorte (snap) de un record, asociado al usuario autenticado
    ---
    security:
      - JWT: []
    tags:
      - Recortes
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            record_id:
              type: string
              description: Id del record sobre el que se crea el recorte.
            type:
              type: string
              description: "Tipo de recorte: document, image, video o audio."
            data:
              type: object
              description: >
                Datos propios del recorte según el tipo (p. ej. bbox {x,y,width,height} y page para
                document/image, begin/end en milisegundos para audio/video).
          required:
            - record_id
            - type
            - data
    responses:
        201:
            description: Recorte creado exitosamente
        404:
            description: El record referenciado (record_id) no existe
        500:
            description: Error creando el recorte (incluye el caso en que falte algún campo requerido en el body)
    """
    user = get_jwt_identity()
    body = request.json

    return services.create(user, body)

@bp.route('/<id>', methods=['DELETE'])
@jwt_required()
def delete_snap(id):
    """
    Eliminar un recorte por su id (solo el usuario propietario del recorte puede eliminarlo)
    ---
    security:
      - JWT: []
    tags:
      - Recortes
    parameters:
      - in: path
        name: id
        schema:
          type: string
        required: true
        description: Id del recorte
    responses:
        204:
            description: Recorte eliminado exitosamente
        401:
            description: El recorte existe pero pertenece a otro usuario
        404:
            description: Recorte no encontrado
        500:
            description: Error eliminando el recorte
    """
    user = get_jwt_identity()

    return services.delete_by_id(id, user)

@bp.route('/<id>', methods=['GET'])
@jwt_required()
def get_snap(id):
    """
    Obtener un recorte por su id (solo el usuario propietario del recorte puede consultarlo)
    ---
    security:
      - JWT: []
    tags:
      - Recortes
    parameters:
      - in: path
        name: id
        schema:
          type: string
        required: true
        description: Id del recorte
    responses:
        200:
            description: >
                Para type=document/image/video: una imagen JPEG recortada (image/jpeg) generada a partir del
                bbox guardado en el recorte. Para type=audio: un stream del fragmento de audio. Para
                cualquier otro type: el documento JSON del recorte (record_id, type, data).
        401:
            description: El recorte existe pero pertenece a otro usuario
        404:
            description: Recorte no encontrado
        500:
            description: Error obteniendo el recorte (incluye fallas al leer/procesar el archivo asociado)
    """
    user = get_jwt_identity()

    resp = services.get_by_id(id, user)
    return resp