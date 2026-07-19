from app.api.records import bp
from app.api.records import public_services
from app.api.users import services as user_services
from flask import request, jsonify
import json

@bp.route('/public/<id>', methods=['GET'])
def get_by_id_public(id):
    """
    Get a record by its id without authentication (only if its accessRights allow public access)
    ---
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
            description: The record has restricted accessRights (not publicly accessible)
        404:
            description: Record does not exist
        500:
            description: Unexpected error
    """
    # Call the service to get a record by its id
    resp = public_services.get_by_id(id)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/public/<id>/stream', methods=['GET'])
def stream_by_id_public(id):
    """
    Get the stream (video/audio/image) of a public record by its id, optionally a time fragment
    ---
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
          description: start of the fragment in seconds (video/audio only); requires end_ms
        - in: query
          name: end_ms
          type: number
          required: false
          description: end of the fragment in seconds (video/audio only); requires start_ms
    responses:
        200:
            description: File (full stream, or a fragment generated with ffmpeg if start_ms/end_ms are specified)
        400:
            description: Invalid start_ms/end_ms (non-numeric, negative, or end_ms less than or equal to start_ms)
        401:
            description: The record has restricted accessRights (not publicly accessible)
        404:
            description: Record does not exist
        500:
            description: Error generating the fragment or another unexpected error
    """
    size = request.args.get('size')
    start_ms = request.args.get('start_ms')
    end_ms = request.args.get('end_ms')
    # Call the service to get a record by its id
    resp = public_services.get_stream(id, size, start_ms, end_ms)
    return resp

@bp.route('/public/<id>/transcription', methods=['POST'])
def get_transcription_by_id_public(id):
    """
    Get the transcription of a public record by its id
    ---
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
                    description: identifier of the processing (plugin) whose transcription is wanted; if omitted, the lookup is done with slug=None
    responses:
        200:
            description: Record transcription
        401:
            description: The record has restricted accessRights (not publicly accessible)
        404:
            description: Record does not exist
        500:
            description: Unexpected error
    """
    body = request.json
    # Call the service to get a record by its id
    resp = public_services.get_transcription(id, body.get('slug'))
    return resp

@bp.route('/public/<id>/pages', methods=['POST'])
def get_page_by_id_public(id):
    """
    Get one or more pages (images) of a public document by its id
    ---
    tags:
        - Records
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: id of the record (or of the resource, if gallery is true)
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
                    description: if true, id is interpreted as a resource and images are retrieved from its gallery instead of pages of a document
            required:
                - pages
                - size
    responses:
        200:
            description: Images of the requested pages
        401:
            description: The record/resource has restricted accessRights (not publicly accessible)
        404:
            description: Record/resource does not exist
        500:
            description: pages/size missing in the body, or another unexpected error
    """
    body = request.json

    # Call the service to get a record by its id
    if 'gallery' in body and body['gallery'] == True:
        return public_services.get_document_gallery(id, body['pages'], body['size'])
    else:
        return public_services.get_document_pages(id, body['pages'], body['size'])

@bp.route('/public/galleryinfo', methods=['POST'])
def get_by_gallery_index_public():
    """
    Get a record from a public resource's image gallery, given the resource id and the image index
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
            description: The record has restricted accessRights (not publicly accessible)
        404:
            description: Record does not exist
        500:
            description: Unexpected error (resource does not exist, index out of range, etc.)
    """
    body = request.json
    # Call the service to get a record by its id
    resp = public_services.get_by_index_gallery(body)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/public/download', methods=['POST'])
def download_public():
    """
    Download the original or processed ("small") file of a public record
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
                    description: id of the record to download
                type:
                    type: string
                    description: 'value "original" (unprocessed original file) or "small" (processed version)'
            required:
                - id
                - type
    responses:
        200:
            description: Record downloaded successfully (attachment)
        400:
            description: id not specified in the body
        401:
            description: The record has restricted accessRights (not publicly accessible)
        404:
            description: The record does not have processing/fileProcessing generated
        500:
            description: Record does not exist, type missing or unsupported, or another unexpected error
    """
    body = request.json
    # Call the service to get a record by its id
    return public_services.download_records(body)
