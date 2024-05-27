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
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

# Nuevo endpoint para obtener el stream de un record por su id
@bp.route('/<id>/stream', methods=['GET'])
@jwt_required()
def get_stream_by_id(id):
    """
    Obtener el stream de un record por su id
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
    resp = services.get_stream(id, current_user)
    
    return resp

@bp.route('/<id>/transcription', methods=['POST'])
@jwt_required()
def get_transcription_by_id(id):
    """
    Obtener la transcripción de un record por su id
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

    body = request.json
    
    # Llamar al servicio para obtener un record por su id
    resp = services.get_transcription(id, body['slug'], current_user)
    
    return resp

@bp.route('/<id>/document', methods=['GET'])
@jwt_required()
def get_document_by_id(id):
    """
    Obtener el documento de un record por su id
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
    return services.get_document(id, current_user)

@bp.route('/<id>/pages', methods=['POST'])
@jwt_required()
def get_page_by_id(id):
    """
    Obtener una página de un record por su id
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
        - in: query
            name: pages
            schema:
                type: array
                required: true
                description: número de páginas a obtener
            name: size
            schema:
                type: string
                required: true
                description: tamaño de la página a obtener small/large
    responses:
        200:
            description: Imagen de la página
        401:
            description: No tiene permisos para obtener un record
        404:
            description: Record no existe
        500:
            description: Error al obtener el record
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    body = request.json

    # Llamar al servicio para obtener un record por su id
    return services.get_document_pages(id, body['pages'], body['size'], current_user)

@bp.route('/<id>/blocks', methods=['POST'])
@jwt_required()
def get_blocks_by_id(id):
    """
    Obtener los bloques de un record por su id
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
        - in: query
            name: blocks
            schema:
                type: array
                required: true
                description: número de bloques a obtener
    responses:
        200:
            description: Bloques del record
        401:
            description: No tiene permisos para obtener un record
        404:
            description: Record no existe
        500:
            description: Error al obtener el record
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    body = request.json
    if 'page' not in body:
        return {'msg': 'Debe especificar una página'}, 500
    if 'block' not in body:
        return {'msg': 'Debe especificar un bloque'}, 500
    if 'slug' not in body:
        return {'msg': 'Debe especificar un slug'}, 500
    
    # Llamar al servicio para obtener un record por su id
    resp = services.get_document_block_by_page(current_user, id, body['page'], body['slug'], body['block'])

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/setBlock', methods=['POST'])
@jwt_required()
def post_label():
    """
    Agregar un bloque de un record
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
                id:
                    type: string
                label:
                    type: string
    responses:
        200:
            description: Label asignado exitosamente
        401:
            description: No tiene permisos para asignar un label a un record
        500:
            description: Error al asignar un label a un record
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # si el usuario no es admin
    if not user_services.has_role(current_user, 'admin') or not user_services.has_role(current_user, 'editor'):
        # retornar error
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Obtener el body del request
    body = request.json

    # Llamar al servicio para asignar un label a un record  
    return services.postBlockDocument(current_user, body)

@bp.route('/setBlock', methods=['PUT'])
@jwt_required()
def set_label():
    """
    Actualizar un bloque de un record
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
                id:
                    type: string
                label:
                    type: string
    responses:
        200:
            description: Label asignado exitosamente
        401:
            description: No tiene permisos para asignar un label a un record
        500:
            description: Error al asignar un label a un record
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # si el usuario no es admin
    if not user_services.has_role(current_user, 'admin') or not user_services.has_role(current_user, 'editor'):
        # retornar error
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Obtener el body del request
    body = request.json

    # Llamar al servicio para asignar un label a un record
    return services.updateBlockDocument(current_user, body)

@bp.route('/setBlock', methods=['DELETE'])
@jwt_required()
def delete_label():
    """
    Actualizar un bloque de un record
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
                id:
                    type: string
                label:
                    type: string
    responses:
        200:
            description: Label asignado exitosamente
        401:
            description: No tiene permisos para asignar un label a un record
        500:
            description: Error al asignar un label a un record
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # si el usuario no es admin
    if not user_services.has_role(current_user, 'admin') or not user_services.has_role(current_user, 'editor'):
        # retornar error
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Obtener el body del request
    body = request.json

    # Llamar al servicio para asignar un label a un record
    return services.deleteBlockDocument(current_user, body)

@bp.route('/favcount/<record_id>', methods=['GET'])
@jwt_required()
def favcount(record_id):
    """
    Obtener el favCount de un record por su id
    ---
    security:
        - JWT: []
    tags:
        - Records
    parameters:
        - in: path
          name: record_id
          schema:
            type: string
            required: true
            description: id del record a obtener
    responses:
        200:
            description: FavCount del record
        500:
            description: Error al obtener el record
    """
    # Llamar al servicio para obtener un record por su id
    return services.get_favCount(record_id)