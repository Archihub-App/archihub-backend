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
    Obtener records paginados según un filtro de Mongo (solo administradores)
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
                    description: filtro de Mongo aplicado a la colección records
                page:
                    type: integer
                    description: número de página (20 resultados por página, empezando en 0)
            required:
                - filters
                - page
    responses:
        200:
            description: Records que cumplen el filtro (cada elemento incluye el total de resultados en el campo total)
        401:
            description: El usuario no tiene rol admin, o el token JWT es inválido/no fue enviado
        404:
            description: Ningún record cumple el filtro
        500:
            description: Error inesperado, incluyendo filters/page ausentes en el body
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
    Obtener un record por su id, si el usuario tiene permisos de acceso
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
          description: id del record a obtener
    responses:
        200:
            description: Record
        401:
            description: El accessRights del record no lo permite al usuario actual, o el token JWT es inválido/no fue enviado
        404:
            description: Record no existe
        500:
            description: Error inesperado
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
    Obtener un record de la galería de imágenes de un recurso, dado el id del recurso y el índice de la imagen
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
                    description: id del recurso (resource) que contiene la galería
                index:
                    type: integer
                    description: índice de la imagen dentro de filesObj a obtener
            required:
                - id
                - index
    responses:
        200:
            description: Record de la imagen en la posición solicitada
        400:
            description: id o index no especificado en el body
        401:
            description: El accessRights del record no lo permite al usuario actual
        404:
            description: Record no existe
        500:
            description: Error inesperado (recurso no existe, índice fuera de rango, etc.)
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
    Obtener el stream (video/audio/imagen) de un record por su id, opcionalmente un fragmento de tiempo
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
          description: id del record a obtener
        - in: query
          name: size
          type: string
          required: false
          description: tamaño de la imagen (small, medium, large); solo aplica a records de tipo imagen
        - in: query
          name: start_ms
          type: number
          required: false
          description: inicio del fragmento en segundos (solo video/audio); requiere end_ms
        - in: query
          name: end_ms
          type: number
          required: false
          description: fin del fragmento en segundos (solo video/audio); requiere start_ms
    responses:
        200:
            description: Archivo (stream completo, o fragmento generado con ffmpeg si se especifican start_ms/end_ms)
        400:
            description: start_ms/end_ms inválidos (no numéricos, negativos, o end_ms menor/igual a start_ms)
        500:
            description: Record no existe o sin permiso de acceso (ambos casos colapsan a 500 en este endpoint), o error generando el fragmento
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
    Descargar el archivo original o procesado ("small") de un record
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
                    description: id del record a descargar
                type:
                    type: string
                    description: 'valor "original" (archivo original sin procesar) o "small" (versión procesada)'
            required:
                - id
                - type
    responses:
        200:
            description: Archivo descargado (attachment)
        400:
            description: La descarga de archivos está desactivada en la configuración del sistema, o id no especificado en el body
        404:
            description: El record no tiene processing/fileProcessing generado
        500:
            description: Record no existe, sin permiso de acceso, type no soportado, u otro error inesperado
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
    Obtener la transcripción (resultado de un procesamiento av_transcribe) de un record por su id
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
          description: id del record
        - in: body
          name: body
          schema:
            type: object
            properties:
                slug:
                    type: string
                    description: identificador del procesamiento (plugin) del que se quiere la transcripción
                page:
                    type: integer
                    description: página de segmentos a obtener (por defecto 0)
            required:
                - slug
    responses:
        200:
            description: Transcripción del record
        500:
            description: Record no existe, sin permiso de acceso, slug ausente en el body, u otro error inesperado
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
    Editar un segmento de la transcripción (texto, tiempos y speaker) de un record por su id
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
          description: id del record
        - in: body
          name: body
          schema:
            type: object
            properties:
                slug:
                    type: string
                    description: identificador del procesamiento (plugin) de la transcripción
                index:
                    type: integer
                    description: índice del segmento a editar
                text:
                    type: string
                    description: nuevo texto del segmento
                start:
                    type: number
                    description: nuevo tiempo de inicio del segmento
                end:
                    type: number
                    description: nuevo tiempo de fin del segmento
                speaker:
                    type: string
                    description: nuevo speaker del segmento (opcional)
            required:
                - slug
                - index
                - text
                - start
                - end
    responses:
        200:
            description: Segmento de transcripción editado exitosamente
        401:
            description: El usuario no tiene rol admin/editor/transcriber, o (si es transcriber) no tiene una tarea asignada sobre este record en estado review/pending/rejected
        404:
            description: Record no existe, no tiene transcripción, no tiene el slug indicado, o el slug no corresponde a un procesamiento av_transcribe
        500:
            description: Record no existe o sin permiso de acceso (verificación inicial), u otro error inesperado
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
    Renombrar un speaker en todos los segmentos de la transcripción de un record
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
        description: id del record
      - in: body
        name: body
        schema:
          type: object
          properties:
              slug:
                  type: string
                  description: identificador del procesamiento (plugin) de la transcripción
              speaker:
                  type: string
                  description: nuevo nombre del speaker (junto con oldSpeaker aplica el renombrado en los segmentos que coincidan)
              oldSpeaker:
                  type: string
                  description: nombre actual del speaker a reemplazar
          required:
              - slug
    responses:
        200:
            description: Speaker editado exitosamente
        401:
            description: El usuario no tiene rol admin/editor/transcriber, o (si es transcriber) no tiene una tarea asignada sobre este record en estado review/pending/rejected
        404:
            description: Record no existe, no tiene transcripción, no tiene el slug indicado, o el slug no corresponde a un procesamiento av_transcribe
        500:
            description: Record no existe o sin permiso de acceso (verificación inicial), u otro error inesperado
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
    Eliminar un segmento de la transcripción de un record por su id
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
          description: id del record
        - in: body
          name: body
          schema:
            type: object
            properties:
                slug:
                    type: string
                    description: identificador del procesamiento (plugin) de la transcripción
                index:
                    type: integer
                    description: índice del segmento a eliminar
            required:
                - slug
                - index
    responses:
        200:
            description: Segmento de transcripción eliminado exitosamente
        401:
            description: El usuario no tiene rol admin/editor/transcriber, o (si es transcriber) no tiene una tarea asignada sobre este record en estado review/pending/rejected
        404:
            description: Record no existe, no tiene transcripción, no tiene el slug indicado, o el slug no corresponde a un procesamiento av_transcribe
        500:
            description: Record no existe o sin permiso de acceso (verificación inicial), u otro error inesperado
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
    Obtener los metadatos de un procesamiento (plugin) de un record por su id y slug
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
          description: id del record
        - in: body
          name: body
          schema:
            type: object
            properties:
                slug:
                    type: string
                    description: identificador del procesamiento (plugin) del que se quieren los metadatos
            required:
                - slug
    responses:
        200:
            description: Metadatos del procesamiento
        500:
            description: Record no existe, sin permiso de acceso, slug ausente en el body, u otro error inesperado
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
    Obtener el resultado de un procesamiento (plugin) de un record por su id y slug
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
          description: id del record
        - in: body
          name: body
          schema:
            type: object
            properties:
                slug:
                    type: string
                    description: identificador del procesamiento (plugin) del que se quiere el resultado
            required:
                - slug
    responses:
        200:
            description: Resultado del procesamiento
        500:
            description: Record no existe, sin permiso de acceso, slug ausente en el body, u otro error inesperado
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
    Obtener el detalle (páginas en baja resolución) de un documento por su id
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
          description: id del record
    responses:
        200:
            description: Detalle del documento
        500:
            description: Record no existe o sin permiso de acceso, u otro error inesperado
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    # Llamar al servicio para obtener un record por su id
    return services.get_document(id, current_user)

@bp.route('/<id>/pages', methods=['POST'])
@jwt_required()
def get_page_by_id(id):
    """
    Obtener una o varias páginas (imágenes) de un documento por su id
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
          description: id del record (o del recurso, si gallery es true)
        - in: body
          name: body
          schema:
            type: object
            properties:
                pages:
                    type: array
                    items:
                        type: string
                    description: números de página a obtener
                size:
                    type: string
                    description: tamaño de las páginas a obtener (small/large)
                gallery:
                    type: boolean
                    description: si es true, id se interpreta como un recurso (resource) y se obtienen imágenes de su galería en lugar de páginas de un documento
            required:
                - pages
                - size
    responses:
        200:
            description: Imágenes de las páginas solicitadas
        500:
            description: Record/recurso no existe o sin permiso de acceso, pages/size ausentes en el body, u otro error inesperado
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
    Obtener los bloques (OCR/layout) de una página de un record por su id
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
          description: id del record
        - in: body
          name: body
          schema:
            type: object
            properties:
                page:
                    type: integer
                    description: número de página
                block:
                    description: identificador/índice del bloque a obtener
                slug:
                    type: string
                    description: identificador del procesamiento (plugin) del que se quieren los bloques
            required:
                - page
                - block
                - slug
    responses:
        200:
            description: Bloques de la página solicitada
        500:
            description: page, block o slug ausentes en el body (el endpoint responde 500, no 400, en este caso), record no existe, u otro error inesperado
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
    Agregar un bloque a un procesamiento de un record (solo admin/editor)
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
                    description: id del record a modificar
                type_block:
                    type: string
                    description: tipo de bloque a agregar; actualmente solo "blocks" está implementado
                slug:
                    type: string
                    description: identificador del procesamiento (plugin) a modificar
                page:
                    type: integer
                    description: número de página (1-indexado) donde se agrega el bloque
                bbox:
                    description: coordenadas del bloque
                data:
                    type: object
                    description: datos adicionales del bloque (se combinan con bbox en el nuevo bloque)
            required:
                - id_doc
                - type_block
                - slug
                - page
                - bbox
                - data
    responses:
        200:
            description: Bloque agregado exitosamente
        401:
            description: El usuario no tiene rol admin ni editor
        404:
            description: Record no existe
        500:
            description: Error inesperado (por ejemplo, campos requeridos ausentes en el body)
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
    Actualizar un bloque existente de un procesamiento de un record (solo admin/editor)
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
                    description: id del record a modificar
                type_block:
                    type: string
                    description: tipo de bloque a actualizar; actualmente solo "blocks" está implementado
                slug:
                    type: string
                    description: identificador del procesamiento (plugin) a modificar
                page:
                    type: integer
                    description: número de página (1-indexado) donde está el bloque
                index:
                    type: integer
                    description: índice del bloque dentro de la página
                bbox:
                    description: nuevas coordenadas del bloque
                data:
                    type: object
                    description: pares clave/valor a actualizar en el bloque
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
            description: Bloque actualizado exitosamente
        401:
            description: El usuario no tiene rol admin ni editor
        404:
            description: Record no existe
        500:
            description: Error inesperado (por ejemplo, campos requeridos ausentes en el body)
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
    Eliminar un bloque existente de un procesamiento de un record (solo admin/editor)
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
                    description: id del record a modificar
                type_block:
                    type: string
                    description: tipo de bloque a eliminar; actualmente solo "blocks" está implementado
                slug:
                    type: string
                    description: identificador del procesamiento (plugin) a modificar
                page:
                    type: integer
                    description: número de página (1-indexado) donde está el bloque
                index:
                    type: integer
                    description: índice del bloque a eliminar dentro de la página
            required:
                - id_doc
                - type_block
                - slug
                - page
                - index
    responses:
        200:
            description: Bloque eliminado exitosamente
        401:
            description: El usuario no tiene rol admin ni editor
        404:
            description: Record no existe
        500:
            description: Error inesperado (por ejemplo, campos requeridos ausentes en el body)
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
    Obtener el número de favoritos (favCount) de un record por su id
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
          description: id del record a consultar
    responses:
        200:
            description: Número de favoritos del record (entero)
        404:
            description: Record no existe
        500:
            description: Error inesperado
    """
    # Llamar al servicio para obtener un record por su id
    return services.get_favCount(record_id)
