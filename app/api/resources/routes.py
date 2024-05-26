from app.api.resources import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.resources import services
from app.api.users import services as user_services
from flask import request, jsonify
import json
from app.utils.functions import cache_type_roles

# En este archivo se registran las rutas de la API para los recursos

# Nuevo endpoint para obtener todos los recursos dado un tipo de contenido y un body de filtros
@bp.route('/<post_type>', methods=['POST'])
@jwt_required()
def get_all(post_type):
    """
    Obtener todos los recursos dado un tipo de contenido y un body de filtros
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
            description: Recursos obtenidos exitosamente
        401:
            description: No tiene permisos para obtener los recursos
        500:
            description: Error al obtener los recursos
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Obtener el body del request
    body = request.json
    
    # convertir a cadena de texto el body
    body = json.dumps(body)

    # Llamar al servicio para obtener los recursos
    resp = services.get_all(post_type, body, current_user)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

# Nuevo endpoint para guardar un recurso nuevo
@bp.route('', methods=['POST'])
@jwt_required()
def create():
    """
    Crear un recurso nuevo con el body del request y agrega al contador de recursos del tipo de contenido
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
                post_type:
                    type: string
                metadata:
                    type: object
                files:
                    type: array
                    items:
                        type: object
                        properties:
                            name:
                                type: string
                            file:
                                type: string
                ident:
                    type: string

    responses:
        201:
            description: Recurso creado exitosamente
        401:
            description: No tiene permisos para crear un recurso
        500:
            description: Error al crear el recurso
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401

    # Obtener el body del request
    body = request.form.to_dict()
    data = body['data']
    # convertir data una cadena de texto JSON stringify a un diccionario
    data = json.loads(data)

    post_type = data['post_type']
    post_type_roles = cache_type_roles(post_type)
    if post_type_roles['editRoles']:
        canEdit = False
        for r in post_type_roles['editRoles']:
            if user_services.has_role(current_user, r) or user_services.has_role(current_user, 'admin'):
                canEdit = True
        if not canEdit:
            return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401

    files = request.files.getlist('files')

    # Llamar al servicio para crear el recurso
    return services.create(data, current_user, files)

# Nuevo endpoint para obtener un recurso por su id
@bp.route('/<id>', methods=['GET'])
@jwt_required()
def get_by_id(id):
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
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Llamar al servicio para obtener el recurso
    resp = services.get_by_id(id, current_user)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

# Nuevo endpoint para actualizar un recurso por su id
@bp.route('/<id>', methods=['PUT'])
@jwt_required()
def update_by_id(id):
    """
    Actualizar un recurso por su id
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
        - in: body
          name: body
          schema:
            type: object
            properties:
                metadata:
                    type: object
                files:
                    type: array
                    items:
                        type: object
                        properties:
                            name:
                                type: string
                            file:
                                type: string
                ident:
                    type: string
    responses:
        200:
            description: Recurso actualizado exitosamente
        401:
            description: No tiene permisos para actualizar el recurso
        500:
            description: Error al actualizar el recurso
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    
    body = request.form.to_dict()
    data = body['data']
    # convertir data una cadena de texto JSON stringify a un diccionario
    data = json.loads(data)

    post_type = data['post_type']
    post_type_roles = cache_type_roles(post_type)
    if post_type_roles['editRoles']:
        canEdit = False
        for r in post_type_roles['editRoles']:
            if user_services.has_role(current_user, r) or user_services.has_role(current_user, 'admin'):
                canEdit = True
        if not canEdit:
            return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401

    files = request.files.getlist('files')

    # Llamar al servicio para crear el recurso
    resp = services.update_by_id(id, data, current_user, files)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp
    # return 'ok'

# Nuevo endpoint para eliminar un recurso por su id
@bp.route('/<id>', methods=['DELETE'])
@jwt_required()
def delete_by_id(id):
    """
    Eliminar un recurso por su id
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
            description: Recurso eliminado exitosamente
        401:
            description: No tiene permisos para eliminar el recurso
        500:
            description: Error al eliminar el recurso
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Llamar al servicio para eliminar el recurso
    return services.delete_by_id(id, current_user)

# Nuevo endpoint para obtener las estructura de arból de un tipo de contenido y sus recursos
@bp.route('/tree', methods=['POST'])
@jwt_required()
def get_tree():
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
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Obtener el body del request
    body = request.json

    slugs = [item['slug'] for item in body['tree']]

    return_slugs = []

    for s in slugs:
        roles = cache_type_roles(s)
        if roles['viewRoles']:
            for r in roles['viewRoles']:
                if user_services.has_role(current_user, r) or user_services.has_role(current_user, 'admin'):
                    return_slugs.append(s)
        else:
            return_slugs.append(s)
    # Llamar al servicio para obtener la estructura de arból
    resp = services.get_tree(body['root'],'|'.join(return_slugs), current_user)
    
    if isinstance(resp, list):
        resp = tuple(resp)
    
    return resp

# Nuevo endpoint para obtener los recursos de un recurso padre
@bp.route('/<resource_id>/records', methods=['POST'])
@jwt_required()
def get_all_records(resource_id):
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
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    body = request.json

    # Llamar al servicio para obtener los recursos
    resp = services.get_resource_files(resource_id, current_user, body['page'])
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp
    
@bp.route('/favcount/<resource_id>', methods=['GET'])
@jwt_required()
def favcount(resource_id):
    """
    Obtener el contador de favoritos de un recurso
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
            description: Contador de favoritos obtenido exitosamente
        500:
            description: Error al obtener el contador de favoritos
    """
    # Llamar al servicio para obtener el contador de favoritos
    return services.get_favCount(resource_id)
