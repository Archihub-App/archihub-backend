from app.api.resources import bp
from app.api.resources import public_services
from flask import request, jsonify
import json
from app.utils.functions import cache_type_roles

@bp.route('/getall/public', methods=['POST'])
def get_all_public():
    """
    Obtener todos los resources dado un body de filtros
    ---
    tags:
        - Resources
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
            description: Resources obtenidos exitosamente
        401:
            description: No tiene permisos para obtener los resources
        500:
            description: Error al obtener los resources
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
    Obtener un recurso por su id
    ---
    security:
        - JWT: []
    tags:
        - Recursos
    parameters:
        - in: path
          name: id
          schema:
            type: string
    responses:
        200:
            description: Recurso obtenido exitosamente
        401:
            description: No tiene permisos para obtener el recurso
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
    Obtener los archivos de un recurso padre
    ---
    security:
        - JWT: []
    tags:
        - Recursos
    parameters:
        - in: path
          name: resource_id
          schema:
              type: string
    responses:
        200:
            description: Recursos obtenidos exitosamente
        401:
            description: No tiene permisos para obtener los recursos
        500:
            description: Error al obtener los recursos
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
    Obtener las estructura de arból de un tipo de contenido y sus recursos
    ---
    security:
        - JWT: []
    tags:
        - Recursos
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                root:
                    type: string
                parents:
                    type: object
    responses:
        200:
            description: Estructura de arból obtenida exitosamente
        401:
            description: No tiene permisos para obtener la estructura de arból
        500:
            description: Error al obtener la estructura de arból
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
    Obtener los archivos de un recurso padre
    ---
    security:
        - JWT: []
    tags:
        - Recursos
    parameters:
        - in: path
          name: resource_id
          schema:
              type: string
    responses:
        200:
            description: Recursos obtenidos exitosamente
        401:
            description: No tiene permisos para obtener los recursos
        500:
            description: Error al obtener los recursos
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
    Descargar un record por su id
    ---
    """
    body = request.json
    
    return public_services.download_resource_files(body)