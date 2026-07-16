from app.api.records import bp
from app.api.records import public_services
from app.api.users import services as user_services
from flask import request, jsonify
import json

@bp.route('/public/<id>', methods=['GET'])
def get_by_id_public(id):
    """
    Obtener un record por su id sin autenticación (solo si su accessRights permite el acceso público)
    ---
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
            description: El record tiene un accessRights restringido (no es de acceso público)
        404:
            description: Record no existe
        500:
            description: Error inesperado
    """
    # Llamar al servicio para obtener un record por su id
    resp = public_services.get_by_id(id)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/public/<id>/stream', methods=['GET'])
def stream_by_id_public(id):
    """
    Obtener el stream (video/audio/imagen) de un record público por su id, opcionalmente un fragmento de tiempo
    ---
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
        401:
            description: El record tiene un accessRights restringido (no es de acceso público)
        404:
            description: Record no existe
        500:
            description: Error generando el fragmento u otro error inesperado
    """
    size = request.args.get('size')
    start_ms = request.args.get('start_ms')
    end_ms = request.args.get('end_ms')
    # Llamar al servicio para obtener un record por su id
    resp = public_services.get_stream(id, size, start_ms, end_ms)
    return resp

@bp.route('/public/<id>/transcription', methods=['POST'])
def get_transcription_by_id_public(id):
    """
    Obtener la transcripción de un record público por su id
    ---
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
                    description: identificador del procesamiento (plugin) del que se quiere la transcripción; si se omite, la búsqueda se hace con slug=None
    responses:
        200:
            description: Transcripción del record
        401:
            description: El record tiene un accessRights restringido (no es de acceso público)
        404:
            description: Record no existe
        500:
            description: Error inesperado
    """
    body = request.json
    # Llamar al servicio para obtener un record por su id
    resp = public_services.get_transcription(id, body.get('slug'))
    return resp

@bp.route('/public/<id>/pages', methods=['POST'])
def get_page_by_id_public(id):
    """
    Obtener una o varias páginas (imágenes) de un documento público por su id
    ---
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
        401:
            description: El record/recurso tiene un accessRights restringido (no es de acceso público)
        404:
            description: Record/recurso no existe
        500:
            description: pages/size ausentes en el body, u otro error inesperado
    """
    body = request.json

    # Llamar al servicio para obtener un record por su id
    if 'gallery' in body and body['gallery'] == True:
        return public_services.get_document_gallery(id, body['pages'], body['size'])
    else:
        return public_services.get_document_pages(id, body['pages'], body['size'])

@bp.route('/public/galleryinfo', methods=['POST'])
def get_by_gallery_index_public():
    """
    Obtener un record de la galería de imágenes de un recurso público, dado el id del recurso y el índice de la imagen
    ---
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
            description: El record tiene un accessRights restringido (no es de acceso público)
        404:
            description: Record no existe
        500:
            description: Error inesperado (recurso no existe, índice fuera de rango, etc.)
    """
    body = request.json
    # Llamar al servicio para obtener un record por su id
    resp = public_services.get_by_index_gallery(body)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/public/download', methods=['POST'])
def download_public():
    """
    Descargar el archivo original o procesado ("small") de un record público
    ---
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
            description: Record descargado exitosamente (attachment)
        400:
            description: id no especificado en el body
        401:
            description: El record tiene un accessRights restringido (no es de acceso público)
        404:
            description: El record no tiene processing/fileProcessing generado
        500:
            description: Record no existe, type ausente o no soportado, u otro error inesperado
    """
    body = request.json
    # Llamar al servicio para obtener un record por su id
    return public_services.download_records(body)
