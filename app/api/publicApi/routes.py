from app.api.publicApi import bp
from flask import jsonify
from flask import request
from app.utils.FernetAuth import publicFernetAuthenticate as fernetAuthenticate
import json

@bp.route('', methods=['POST'])
@fernetAuthenticate
def get_all(username, isAdmin):
    """
    Obtener recursos publicados: listado paginado por tipo, o búsqueda por palabra clave
    ---
    security:
        - JWT: []
    tags:
        - Api Pública
    description: >
        Requiere un token Fernet cifrado de la Api Pública (header `Authorization: Bearer <token>`,
        distinto del JWT normal de `/auth/login`; ver app.utils.FernetAuth.publicFernetAuthenticate).
        Si `keyword` viene no vacío en el body, la búsqueda se delega en
        app.api.search.public_services.get_resources_by_filters (Elasticsearch o vector DB,
        según las capacidades activas del sistema). En caso contrario se delega en
        app.api.resources.public_services.get_all, que requiere `post_type` (lista de slugs) y
        solo devuelve recursos con `status: "published"`.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                keyword:
                    type: string
                    description: Si viene no vacío, activa el flujo de búsqueda en vez del listado
                searchSource:
                    type: string
                    description: "'index' (Elasticsearch, por defecto) o 'vector' — solo aplica con keyword"
                post_type:
                    type: array
                    items:
                        type: string
                    description: Requerido cuando no hay keyword; slugs de los tipos de contenido a listar
                page:
                    type: integer
                activeColumns:
                    type: array
                    items:
                        type: object
                parents:
                    type: object
                    description: "{'id': ...} para filtrar por padre directo"
                files:
                    type: boolean
                    description: Si es true, filtra solo recursos que tengan archivos asociados
                sortBy:
                    type: string
                    default: createdAt
                sortOrder:
                    type: string
                    default: asc
    responses:
        200:
            description: Recursos obtenidos exitosamente
        401:
            description: Un tipo de contenido solicitado tiene restricción de rol de visualización que el usuario no cumple
        500:
            description: Error al obtener los recursos (p.ej. falta "post_type" en el body sin keyword)
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
    Obtener todos los tipos de contenido (delega en app.api.types.services.get_all)
    ---
    security:
        - JWT: []
    tags:
        - Api Pública
    description: Requiere un token Fernet cifrado de la Api Pública (ver descripción de POST /publicApi).
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
    Obtener un recurso publicado por su ID (delega en app.api.resources.public_services.get_by_id)
    ---
    security:
        - JWT: []
    tags:
        - Api Pública
    description: Requiere un token Fernet cifrado de la Api Pública (ver descripción de POST /publicApi).
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: ObjectId de MongoDB del recurso
    responses:
        200:
            description: Recurso obtenido exitosamente
        401:
            description: Token no provisto/inválido/expirado, o el recurso tiene accessRights/viewRoles que el usuario público no cumple
        500:
            description: >
                Error al obtener el recurso. Nota: un id inexistente o no publicado también
                cae aquí con 500 (excepción genérica "Recurso no existe"), no un 404 dedicado.
    """
    from app.api.resources.public_services import get_by_id as get_by_id_public
    resp =  get_by_id_public(id)
    
    if isinstance(resp, list):
      return tuple(resp)
    else:
      return resp