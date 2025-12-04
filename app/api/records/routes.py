from app.api.records import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.records import services
from app.api.users import services as user_services
from flask import request, jsonify
import json
from flask_babel import _

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
        404:
            description: Records no encontrados
        500:
            description: Error al obtener los records
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # si el usuario no es admin
    if not user_services.has_role(current_user, 'admin'):
        # retornar error
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
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
    
# Nuevo endpoint para obtener un record por su id
@bp.route('/galleryinfo', methods=['POST'])
@jwt_required()
def get_by_gallery_index():
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
        400:
            description: ID o index no especificado
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
    resp = services.get_by_index_gallery(body, current_user)
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

@bp.route('/download', methods=['POST'])
@jwt_required()
def download_records():
    """
    Descargar un conjunto de records
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
                    required: true
                    description: id del record a obtener
                type:
                    type: string
                    description: archivo a descargar (original, small, medium, large)
            
    responses:
        200:
            description: Records descargados exitosamente
        401:
            description: No tiene permisos para descargar los records
        404:
            description: Records con problemas en los procesamientos
        500:
            description: Error al descargar los records
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    # Llamar al servicio para obtener un record por su id
    resp = services.download_records(request.json, current_user)
    
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
            description: Record obtenido exitosamente
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
    resp = services.get_transcription(id, body['slug'], current_user, body.get('page', 0))
    
    return resp

@bp.route('/<id>/edit-transcription', methods=['PUT'])
@jwt_required()
def edit_document_transcription(id):
    """
    Editar la transcripción de un record por su id
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
            description: No tiene permisos para editar un record
        404:
            description: Record no existe o no tiene transcripción
        500:
            description: Error al obtener el record
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'transcriber'):
        # retornar error
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    body = request.json
    
    # Llamar al servicio para obtener un record por su id
    resp = services.edit_transcription(id, body, current_user)

    return resp

@bp.route('/<id>/edit-transcription-speaker', methods=['PUT'])
@jwt_required()
def edit_document_transcription_speaker(id):
    """
    Editar un speaker de una transcripción de un record por su id
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
            description: No tiene permisos para editar un record
        404:
            description: Record no existe o no tiene transcripción
        500:
            description: Error al obtener el record
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'transcriber'):
        # retornar error
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    body = request.json
    
    # Llamar al servicio para obtener un record por su id
    resp = services.edit_transcription_speaker(id, body, current_user)
    
    return resp

@bp.route('/<id>/edit-transcription', methods=['DELETE'])
@jwt_required()
def delete_document_transcription(id):
    """
    Eliminar la transcripción de un record por su id
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
            description: Record no existe o no tiene transcripción
        500:
            description: Error al obtener el record
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor') and not user_services.has_role(current_user, 'transcriber'):
        # retornar error
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    body = request.json
    
    # Llamar al servicio para obtener un record por su id
    resp = services.delete_transcription_segment(id, body, current_user)

    return resp

@bp.route('/<id>/metadata', methods=['POST'])
@jwt_required()
def get_metadata_by_id(id):
    """
    Obtener el metadata de un processing record por su id y slug
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
    resp = services.get_processing_metadata(id, body['slug'], current_user)
    
    return resp

@bp.route('/<id>/result', methods=['POST'])
@jwt_required()
def get_result_by_id(id):
    """
    Obtener el resultado de un processing record por su id y slug
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
            name: slug
            schema:
                type: string
                required: true
                description: slug del processing a obtener
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
    resp = services.get_processing_result(id, body['slug'], current_user)
    
    return resp

@bp.route('/<id>/document', methods=['GET'])
@jwt_required()
def get_document_by_id(id):
    """
    Obtener el resultado de un processing record por su id y slug
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
              items:
                  type: string
          required: true
          description: número de páginas a obtener
        - in: query
          name: size
          schema:
              type: string
          required: true
          description: tamaño de la página a obtener (small/large)
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
    if 'gallery' in body and body['gallery'] == True:
        return services.get_document_gallery(id, body['pages'], body['size'], current_user)
    else:
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
        return {'msg': _('You must specify a page')}, 500
    if 'block' not in body:
        return {'msg': _('You must specify a block')}, 500
    if 'slug' not in body:
        return {'msg': _('You must specify a slug')}, 500
    
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
    Agregar un bloque a un record
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
                type_block:
                    type: string
                page:
                    type: integer
                data:
                    type: object
    responses:
        201:
            description: Label asignado exitosamente
        401:
            description: No tiene permisos para asignar un label a un record
        404:
            description: Record no existe
        500:
            description: Error al asignar un label a un record
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # si el usuario no es admin
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        # retornar error
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
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
                type_block:
                    type: string
                page:
                    type: integer
                data:
                    type: object
    responses:
        200:
            description: Bloque actualizado exitosamente
        401:
            description: No tiene permisos para actualizar un bloque de un record
        404:
            description: Record no existe
        500:
            description: Error al actualizar un bloque de un record
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # si el usuario no es admin
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        # retornar error
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.json

    # Llamar al servicio para asignar un label a un record
    return services.updateBlockDocument(current_user, body)

@bp.route('/setBlock', methods=['DELETE'])
@jwt_required()
def delete_label():
    """
    Eliminar un bloque de un record
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
    responses:
        200:
            description: Bloque eliminado exitosamente
        401:
            description: No tiene permisos para eliminar un bloque de un record
        404:
            description: Record no existe
        500:
            description: Error al eliminar un bloque de un record
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # si el usuario no es admin
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        # retornar error
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
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
        404:
            description: Record no existe
        500:
            description: Error al obtener el record
    """
    # Llamar al servicio para obtener un record por su id
    return services.get_favCount(record_id)