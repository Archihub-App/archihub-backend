from app.api.records import bp
from app.api.records import public_services
from app.api.users import services as user_services
from flask import request, jsonify
import json

@bp.route('/public/<id>', methods=['GET'])
def get_by_id_public(id):
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
    # Llamar al servicio para obtener un record por su id
    resp = public_services.get_by_id(id)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp
    
@bp.route('/public/<id>/stream', methods=['GET'])
def stream_by_id_public(id):
    """
    Obtener un stream de un record por su id
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
            description: Stream del record
        401:
            description: No tiene permisos para obtener un record
        404:
            description: Record no existe
        500:
            description: Error al obtener el record
    """
    # Llamar al servicio para obtener un record por su id
    resp = public_services.get_stream(id)
    return resp

@bp.route('/public/<id>/transcription', methods=['POST'])
def get_transcription_by_id_public(id):
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
            description: Transcripción del record
        401:
            description: No tiene permisos para obtener un record
        404:
            description: Record no existe
        500:
            description: Error al obtener el record
    """
    body = request.json
    # Llamar al servicio para obtener un record por su id
    resp = public_services.get_transcription(id, body.get('slug'))
    return resp

@bp.route('/public/<id>/pages', methods=['POST'])
def get_page_by_id_public(id):
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
    body = request.json

    # Llamar al servicio para obtener un record por su id
    if 'gallery' in body and body['gallery'] == True:
        return public_services.get_document_gallery(id, body['pages'], body['size'])
    else:
        return public_services.get_document_pages(id, body['pages'], body['size'])

@bp.route('/public/galleryinfo', methods=['POST'])
def get_by_gallery_index_public():
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
    Descargar un record por su id
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
            description: Record descargado exitosamente
        400:
            description: ID no definido
        404:
            description: Records con problemas en los procesamientos
        500:
            description: Error al descargar los records
    """
    body = request.json
    # Llamar al servicio para obtener un record por su id
    return public_services.download_records(body)