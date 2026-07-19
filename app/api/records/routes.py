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
    Get paginated records matching a Mongo filter (admins only)
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
                    description: Mongo filter applied to the records collection
                page:
                    type: integer
                    description: page number (20 results per page, starting at 0)
            required:
                - filters
                - page
    responses:
        200:
            description: Records matching the filter (each element includes the total result count in the total field)
        401:
            description: The user doesn't have the admin role, or the JWT token is invalid/wasn't sent
        404:
            description: No record matches the filter
        500:
            description: Unexpected error, including filters/page missing from the body
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
    Get a record by its id, if the user has access permission
    ---
    security:
        - JWT: []
    tags:
        - Records
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: id of the record to retrieve
    responses:
        200:
            description: Record
        401:
            description: The record's accessRights doesn't allow the current user, or the JWT token is invalid/wasn't sent
        404:
            description: Record does not exist
        500:
            description: Unexpected error
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
    Get a record from a resource's image gallery, given the resource id and the image index
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
                    description: id of the resource that contains the gallery
                index:
                    type: integer
                    description: index of the image within filesObj to retrieve
            required:
                - id
                - index
    responses:
        200:
            description: Record of the image at the requested position
        400:
            description: id or index not specified in the body
        401:
            description: The record's accessRights doesn't allow the current user
        404:
            description: Record does not exist
        500:
            description: Unexpected error (resource does not exist, index out of range, etc.)
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
    Get the stream (video/audio/image) of a record by its id, optionally a time fragment
    ---
    security:
        - JWT: []
    tags:
        - Records
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: id of the record to retrieve
        - in: query
          name: size
          type: string
          required: false
          description: image size (small, medium, large); only applies to image-type records
        - in: query
          name: start_ms
          type: number
          required: false
          description: fragment start in seconds (video/audio only); requires end_ms
        - in: query
          name: end_ms
          type: number
          required: false
          description: fragment end in seconds (video/audio only); requires start_ms
    responses:
        200:
            description: File (full stream, or fragment generated with ffmpeg if start_ms/end_ms are specified)
        400:
            description: Invalid start_ms/end_ms (non-numeric, negative, or end_ms less than or equal to start_ms)
        500:
            description: Record does not exist or no access permission (both cases collapse to 500 on this endpoint), or error generating the fragment
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    # Obtener el parámetro size de la query string
    size = request.args.get('size')
    start_ms = request.args.get('start_ms')
    end_ms = request.args.get('end_ms')

    # Llamar al servicio para obtener un record por su id
    resp = services.get_stream(id, current_user, size, start_ms, end_ms)

    return resp

@bp.route('/download', methods=['POST'])
@jwt_required()
def download_records():
    """
    Download the original or processed ("small") file of a record
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
                    description: id of the record to download
                type:
                    type: string
                    description: 'value "original" (raw, unprocessed file) or "small" (processed version)'
            required:
                - id
                - type
    responses:
        200:
            description: File downloaded (attachment)
        400:
            description: File downloads are disabled in the system configuration, or id not specified in the body
        404:
            description: The record has no processing/fileProcessing generated
        500:
            description: Record does not exist, no access permission, unsupported type, or another unexpected error
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
    Get the transcription (result of an av_transcribe processing) of a record by its id
    ---
    security:
        - JWT: []
    tags:
        - Records
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: id of the record
        - in: body
          name: body
          schema:
            type: object
            properties:
                slug:
                    type: string
                    description: identifier of the processing (plugin) the transcription is wanted from
                page:
                    type: integer
                    description: page of segments to retrieve (default 0)
            required:
                - slug
    responses:
        200:
            description: Record's transcription
        500:
            description: Record does not exist, no access permission, slug missing from the body, or another unexpected error
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
    Edit a transcription segment (text, times, and speaker) of a record by its id
    ---
    security:
        - JWT: []
    tags:
        - Records
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: id of the record
        - in: body
          name: body
          schema:
            type: object
            properties:
                slug:
                    type: string
                    description: identifier of the transcription's processing (plugin)
                index:
                    type: integer
                    description: index of the segment to edit
                text:
                    type: string
                    description: new segment text
                start:
                    type: number
                    description: new segment start time
                end:
                    type: number
                    description: new segment end time
                speaker:
                    type: string
                    description: new segment speaker (optional)
            required:
                - slug
                - index
                - text
                - start
                - end
    responses:
        200:
            description: Transcription segment edited successfully
        401:
            description: The user doesn't have the admin/editor/transcriber role, or (if transcriber) has no task assigned on this record in review/pending/rejected state
        404:
            description: Record does not exist, has no transcription, doesn't have the given slug, or the slug doesn't correspond to an av_transcribe processing
        500:
            description: Record does not exist or no access permission (initial check), or another unexpected error
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
    Rename a speaker across all segments of a record's transcription
    ---
    security:
        - JWT: []
    tags:
      - Records
    parameters:
      - in: path
        name: id
        type: string
        required: true
        description: id of the record
      - in: body
        name: body
        schema:
          type: object
          properties:
              slug:
                  type: string
                  description: identifier of the transcription's processing (plugin)
              speaker:
                  type: string
                  description: new speaker name (together with oldSpeaker, applies the rename to matching segments)
              oldSpeaker:
                  type: string
                  description: current speaker name to replace
          required:
              - slug
    responses:
        200:
            description: Speaker edited successfully
        401:
            description: The user doesn't have the admin/editor/transcriber role, or (if transcriber) has no task assigned on this record in review/pending/rejected state
        404:
            description: Record does not exist, has no transcription, doesn't have the given slug, or the slug doesn't correspond to an av_transcribe processing
        500:
            description: Record does not exist or no access permission (initial check), or another unexpected error
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
    Delete a transcription segment of a record by its id
    ---
    security:
        - JWT: []
    tags:
        - Records
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: id of the record
        - in: body
          name: body
          schema:
            type: object
            properties:
                slug:
                    type: string
                    description: identifier of the transcription's processing (plugin)
                index:
                    type: integer
                    description: index of the segment to delete
            required:
                - slug
                - index
    responses:
        200:
            description: Transcription segment deleted successfully
        401:
            description: The user doesn't have the admin/editor/transcriber role, or (if transcriber) has no task assigned on this record in review/pending/rejected state
        404:
            description: Record does not exist, has no transcription, doesn't have the given slug, or the slug doesn't correspond to an av_transcribe processing
        500:
            description: Record does not exist or no access permission (initial check), or another unexpected error
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
    Get the metadata of a record's processing (plugin) by its id and slug
    ---
    security:
        - JWT: []
    tags:
        - Records
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: record id
        - in: body
          name: body
          schema:
            type: object
            properties:
                slug:
                    type: string
                    description: identifier of the processing (plugin) the metadata is wanted from
            required:
                - slug
    responses:
        200:
            description: Processing metadata
        500:
            description: Record does not exist, no access permission, slug missing from the body, or another unexpected error
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
    Get the result of a record's processing (plugin) by its id and slug
    ---
    security:
        - JWT: []
    tags:
        - Records
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: record id
        - in: body
          name: body
          schema:
            type: object
            properties:
                slug:
                    type: string
                    description: identifier of the processing (plugin) the result is wanted from
            required:
                - slug
    responses:
        200:
            description: Processing result
        500:
            description: Record does not exist, no access permission, slug missing from the body, or another unexpected error
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
    Get the detail (low-resolution pages) of a document by its id
    ---
    security:
        - JWT: []
    tags:
        - Records
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: record id
    responses:
        200:
            description: Document detail
        500:
            description: Record does not exist or no access permission, or another unexpected error
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    # Llamar al servicio para obtener un record por su id
    return services.get_document(id, current_user)

@bp.route('/<id>/pages', methods=['POST'])
@jwt_required()
def get_page_by_id(id):
    """
    Get one or more pages (images) of a document by its id
    ---
    security:
        - JWT: []
    tags:
        - Records
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: record id (or resource id, if gallery is true)
        - in: body
          name: body
          schema:
            type: object
            properties:
                pages:
                    type: array
                    items:
                        type: string
                    description: page numbers to retrieve
                size:
                    type: string
                    description: size of the pages to retrieve (small/large)
                gallery:
                    type: boolean
                    description: if true, id is interpreted as a resource and images are retrieved from its gallery instead of a document's pages
            required:
                - pages
                - size
    responses:
        200:
            description: Images of the requested pages
        500:
            description: Record/resource does not exist or no access permission, missing pages/size in the body, or another unexpected error
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
    Get the blocks (OCR/layout) of a page of a record by its id
    ---
    security:
        - JWT: []
    tags:
        - Records
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: record id
        - in: body
          name: body
          schema:
            type: object
            properties:
                page:
                    type: integer
                    description: page number
                block:
                    description: identifier/index of the block to retrieve
                slug:
                    type: string
                    description: identifier of the processing (plugin) the blocks are wanted from
            required:
                - page
                - block
                - slug
    responses:
        200:
            description: Blocks of the requested page
        500:
            description: page, block, or slug missing from the body (this endpoint responds 500, not 400, in this case), record does not exist, or another unexpected error
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
    Add a block to a processing of a record (admin/editor only)
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
                id_doc:
                    type: string
                    description: id of the record to modify
                type_block:
                    type: string
                    description: type of block to add; currently only "blocks" is implemented
                slug:
                    type: string
                    description: identifier of the processing (plugin) to modify
                page:
                    type: integer
                    description: page number (1-indexed) where the block is added
                bbox:
                    description: coordinates of the block
                data:
                    type: object
                    description: additional block data (merged with bbox into the new block)
            required:
                - id_doc
                - type_block
                - slug
                - page
                - bbox
                - data
    responses:
        200:
            description: Block added successfully
        401:
            description: Missing admin/editor role
        404:
            description: Record does not exist
        500:
            description: Unexpected error (e.g. required fields missing from the body)
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
    Update an existing block of a processing of a record (admin/editor only)
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
                id_doc:
                    type: string
                    description: id of the record to modify
                type_block:
                    type: string
                    description: type of block to update; currently only "blocks" is implemented
                slug:
                    type: string
                    description: identifier of the processing (plugin) to modify
                page:
                    type: integer
                    description: page number (1-indexed) where the block is
                index:
                    type: integer
                    description: index of the block within the page
                bbox:
                    description: new coordinates of the block
                data:
                    type: object
                    description: key/value pairs to update on the block
            required:
                - id_doc
                - type_block
                - slug
                - page
                - index
                - bbox
                - data
    responses:
        200:
            description: Block updated successfully
        401:
            description: Missing admin/editor role
        404:
            description: Record does not exist
        500:
            description: Unexpected error (e.g. required fields missing from the body)
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
    Delete an existing block of a processing of a record (admin/editor only)
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
                id_doc:
                    type: string
                    description: id of the record to modify
                type_block:
                    type: string
                    description: type of block to delete; currently only "blocks" is implemented
                slug:
                    type: string
                    description: identifier of the processing (plugin) to modify
                page:
                    type: integer
                    description: page number (1-indexed) where the block is
                index:
                    type: integer
                    description: index of the block to delete within the page
            required:
                - id_doc
                - type_block
                - slug
                - page
                - index
    responses:
        200:
            description: Block deleted successfully
        401:
            description: Missing admin/editor role
        404:
            description: Record does not exist
        500:
            description: Unexpected error (e.g. required fields missing from the body)
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
    Get the favorites count (favCount) of a record by its id
    ---
    security:
        - JWT: []
    tags:
        - Records
    parameters:
        - in: path
          name: record_id
          type: string
          required: true
          description: id of the record to query
    responses:
        200:
            description: Favorites count of the record (integer)
        404:
            description: Record does not exist
        500:
            description: Unexpected error
    """
    # Llamar al servicio para obtener un record por su id
    return services.get_favCount(record_id)
