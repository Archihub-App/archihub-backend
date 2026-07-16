from app.api.resources import bp
from app.api.resources import public_services
from flask import request, jsonify
import json
from app.utils.functions import cache_type_roles

@bp.route('/getall/public', methods=['POST'])
def get_all_public():
    """
    Obtener recursos publicados de uno o varios tipos de contenido (sin autenticación)
    ---
    tags:
        - Recursos
    description: >
      No requiere JWT. Solo devuelve recursos con status='published'; si alguno de
      los post_type solicitados tiene viewRoles configurados (acceso restringido),
      la solicitud completa se rechaza con 401.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            required:
                - post_type
            properties:
                post_type:
                    type: array
                    items:
                        type: string
                    description: Requerido; slugs de los tipos de contenido a consultar
                page:
                    type: integer
                    description: Página (límite fijo de 20)
                parents:
                    type: object
                    properties:
                        id:
                            type: string
                files:
                    type: boolean
                    description: Si es true, filtra solo recursos con archivos asociados
                activeColumns:
                    type: array
                    items:
                        type: object
                        properties:
                            destiny:
                                type: string
                sortBy:
                    type: string
                    default: createdAt
                sortOrder:
                    type: string
                    enum: [asc, desc]
                    default: asc
    responses:
        200:
            description: "Objeto { total, resources } con los recursos publicados"
        401:
            description: Alguno de los post_type solicitados tiene viewRoles (no es público)
        500:
            description: Error al obtener los recursos (incluye KeyError si falta post_type)
    """
    body = request.json
    body = json.dumps(body)
    resp = public_services.get_all(body)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/public/<id>', methods=['GET'])
def get_by_id_public(id):
    """
    Obtener un recurso publicado por su id (sin autenticación)
    ---
    tags:
        - Recursos
    description: No requiere JWT. Solo devuelve el recurso si es de acceso público (sin accessRights restrictivos ni viewRoles).
    parameters:
        - in: path
          name: id
          type: string
          required: true
    responses:
        200:
            description: Recurso obtenido exitosamente
        401:
            description: El recurso tiene accessRights o viewRoles que restringen el acceso público
        404:
            description: Recurso no encontrado
        500:
            description: Error al obtener el recurso
    """
    resp = public_services.get_by_id(id)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/public/<resource_id>/records', methods=['POST'])
def get_all_records_public(resource_id):
    """
    Obtener (paginados) los archivos de un recurso publicado (sin autenticación)
    ---
    tags:
        - Recursos
    description: No requiere JWT. Solo funciona si el recurso es de acceso público (sin accessRights restrictivos).
    parameters:
        - in: path
          name: resource_id
          type: string
          required: true
        - in: body
          name: body
          schema:
            type: object
            required:
                - page
            properties:
                page:
                    type: integer
                    description: Requerido (número de página, sin default)
                groupImages:
                    type: boolean
                    description: Si es true, agrupa las imágenes en una sola entrada de galería
    responses:
        200:
            description: "Archivos obtenidos. Body: { data, total }"
        401:
            description: El recurso tiene accessRights que restringen el acceso público
        404:
            description: Recurso no encontrado
        500:
            description: Error al obtener los archivos (incluye KeyError si falta 'page')
    """
    body = request.json

    if 'groupImages' not in body:
        resp = public_services.get_resource_files(resource_id, body['page'])
    else:
        resp = public_services.get_resource_files(resource_id, body['page'], body['groupImages'])
        
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/public/tree', methods=['POST'])
def get_tree_public():
    """
    Obtener el árbol de recursos publicados, sin autenticación ('tree' o 'list')
    ---
    tags:
        - Recursos
    description: >
      No requiere JWT. Solo incluye tipos de contenido sin viewRoles configurados
      (los que sí los tienen se omiten silenciosamente, no dan 401). Requiere
      'view' en el body ('tree' o 'list'); cualquier otro valor (o su ausencia)
      hace que la ruta no devuelva respuesta (falla con 500).
    parameters:
        - in: body
          name: body
          schema:
            type: object
            required:
                - view
                - root
            properties:
                view:
                    type: string
                    enum: [tree, list]
                root:
                    type: string
                    description: Id del recurso raíz, o 'all' para el nivel superior
                tree:
                    type: array
                    description: Requerido si view=tree; lista de { slug }
                    items:
                        type: object
                        properties:
                            slug:
                                type: string
                postType:
                    type: string
                    description: view=list; si se envía (no vacío), se usa junto con sus tipos padre en vez de activeTypes
                activeTypes:
                    type: array
                    items:
                        type: string
                    description: view=list; requerido si postType no se envía o está vacío
                page:
                    type: integer
                    description: view=list; opcional, tamaño de página fijo de 10
    responses:
        200:
            description: view=tree -> array de nodos; view=list -> array de recursos publicados
        500:
            description: Error inesperado (incluye KeyError si faltan campos requeridos, o 'view' ausente/no reconocido)
    """
    try:
        body = request.json

        if 'view' in body:
            if body['view'] == 'tree':
                slugs = [item['slug'] for item in body['tree']]
                return_slugs = []

                for s in slugs:
                    roles = cache_type_roles(s)
                    if not roles['viewRoles']:
                        return_slugs.append(s)

                # Llamar al servicio para obtener la estructura de arból
                resp = public_services.get_tree(body['root'],'|'.join(return_slugs))
                
                if isinstance(resp, list):
                    resp = tuple(resp)
                
                return resp
        
            elif body['view'] == 'list':
                if 'postType' in body:
                    if body['postType']:
                        type = body['postType']
                        from app.api.types.services import get_by_slug
                        type = get_by_slug(type)
                        if isinstance(type, list):
                            type = type[0]
                        from app.api.types.services import get_parents
                        parents = get_parents(type)
                        
                        slugs = [item['slug'] for item in parents]
                        slugs = [type['slug'], *slugs]
                    else:
                        slugs = body['activeTypes']
                elif 'root' in body:
                    slugs = body['activeTypes']

                return_slugs = []

                for s in slugs:
                    roles = cache_type_roles(s)
                    if not roles['viewRoles']:
                        return_slugs.append(s)
                
                resp = public_services.get_tree(body['root'],'|'.join(return_slugs), body['postType'] if 'postType' in body else None, int(body['page']) if 'page' in body else 0)

                if isinstance(resp, list):
                    resp = tuple(resp)
                
                return resp
    except Exception as e:
        return jsonify({'msg': str(e)}), 500

@bp.route('/public/<resource_id>/imgs', methods=['GET'])
def get_imgs_public(resource_id):
    """
    Obtener las imágenes de un recurso publicado (sin autenticación)
    ---
    tags:
        - Recursos
    parameters:
        - in: path
          name: resource_id
          type: string
          required: true
    responses:
        200:
            description: Imágenes obtenidas exitosamente
        404:
            description: Recurso no encontrado, o no tiene imágenes asociadas
        500:
            description: Error al obtener las imágenes
    """
    # Llamar al servicio para obtener los recursos
    resp = public_services.get_resource_images(resource_id)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp
    
@bp.route('/public/download_records', methods=['POST'])
def download_public():
    """
    Descargar el/los archivo(s) de un recurso publicado (sin autenticación)
    ---
    tags:
        - Recursos
    parameters:
        - in: body
          name: body
          schema:
            type: object
            required:
                - id
                - type
            properties:
                id:
                    type: string
                    description: Id del recurso (requerido; debe estar published)
                type:
                    type: string
                    enum: [original, small]
                    description: Requerido; qué variante de los archivos descargar
    produces:
        - application/octet-stream
    responses:
        200:
            description: Archivo binario (attachment); un solo archivo directo, o un .zip si el recurso tiene más de uno
        401:
            description: El recurso tiene accessRights que restringen el acceso público
        404:
            description: El recurso no existe (o no está published), o alguno de sus archivos no existe
        500:
            description: Error inesperado al generar la descarga (incluye KeyError si faltan 'id'/'type')
    """
    body = request.json
    
    return public_services.download_resource_files(body)