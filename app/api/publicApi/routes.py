from app.api.publicApi import bp
from flask import jsonify
from flask import request
from app.utils.FernetAuth import publicFernetAuthenticate as fernetAuthenticate
import json

@bp.route('', methods=['POST'])
@fernetAuthenticate
def get_all(username, isAdmin):
    """
    Obtener todos los recursos
    ---
    security:
        - JWT: []
    tags:
        - Api Pública
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                post_type:
                    type: string
                page:
                    type: integer
                files:
                    type: object
                sort_order:
                    type: string
                sort_by:
                    type: string
    responses:
        200:
            description: Recursos obtenidos exitosamente
        500:
            description: Error al obtener los recursos
    """
    body = request.json

    if 'keyword' in body and body['keyword'] != '':
      from app.api.search.public_services import get_resources_by_filters
      resp = get_resources_by_filters(body)
    else:
      from app.api.resources.public_services import get_all
      resp = get_all(json.dumps(body))

    if isinstance(resp, list):
      return tuple(resp)
    else:
      return resp

@bp.route('/types', methods=['GET'])
@fernetAuthenticate
def get_types(username, isAdmin):
    """
    Obtener todos los tipos de contenido
    ---
    security:
        - JWT: []
    tags:
        - Api Pública
    responses:
        200:
            description: Recursos obtenidos exitosamente
        500:
            description: Error al obtener los recursos
    """
    from app.api.types.services import get_all as get_all_types
    resp = get_all_types()

    if isinstance(resp, list):
      return tuple(resp)
    else:
      return resp

@bp.route('/resources/<id>', methods=['GET'])
@fernetAuthenticate
def get_item(username, isAdmin, id):
    """
    Obtener recurso por ID
    ---
    security:
        - JWT: []
    tags:
        - Api Pública
    parameters:
        - in: path
          name: id
          type: string
          required: true
    responses:
        200:
            description: Recurso obtenido exitosamente
        401:
            description: No tiene permisos para obtener el recurso
        500:
            description: Error al obtener los recursos
    """
    from app.api.resources.public_services import get_by_id as get_by_id_public
    resp =  get_by_id_public(id)
    
    if isinstance(resp, list):
      return tuple(resp)
    else:
      return resp