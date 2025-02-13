from app.api.adminApi import bp
from flask import jsonify
from flask import request
from app.api.adminApi import services
from app.utils.FernetAuth import fernetAuthenticate
import json
from flask_babel import _

# Nuevo POST endpoint para crear nuevos recursos
@bp.route('/create', methods=['POST'])
@fernetAuthenticate
def new_resource(username, isAdmin):
    """
    Crear un nuevo recurso
    ---
    tags:
        - Api de administrador
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
                description:
                    type: string
                metadata:
                    type: array
                    items:
                        type: object
                icon:
                    type: string
                hierarchical:
                    type: boolean
                parentType:
                    type: string
    responses:
        201:
            description: Recurso creado exitosamente
        400:
            description: El recurso no tiene metadata
        401:
            description: No tiene permisos para crear un recurso
        500:
            description: Error al crear el recurso
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.form.to_dict()
    files = request.files.getlist('files')
    data = json.loads(body['data'])
    # Llamar al servicio para crear el recurso
    return services.create(data, username, files)

# Nuevo POST endpoint para actualizar un recurso
@bp.route('/update', methods=['POST'])
@fernetAuthenticate
def update_resource(username, isAdmin):
    """
    Actualizar un recurso
    ---
    tags:
        - Api de administrador
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                id:
                    type: string
                name:
                    type: string
                description:
                    type: string
                metadata:
                    type: array
                    items:
                        type: object
                icon:
                    type: string
                hierarchical:
                    type: boolean
                parentType:
                    type: string
    responses:
        200:
            description: Recurso actualizado exitosamente
        401:
            description: No tiene permisos para actualizar un recurso
        500:
            description: Error al actualizar el recurso
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.form.to_dict()
    files = request.files.getlist('files')
    data = json.loads(body['data'])

    # Llamar al servicio para actualizar el recurso
    return services.update(body['id'], data, username, files)

# Nuevo POST endpoint para obtener el id de un recurso por su nombre
@bp.route('/get_id', methods=['POST'])
@fernetAuthenticate
def get_resource_id(username, isAdmin):
    """
    Obtener el id de un recurso por su nombre
    ---
    tags:
        - Api de administrador
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
    responses:
        200:
            description: Id del recurso obtenido exitosamente
        400:
            description: El recurso no existe
        401:
            description: No tiene permisos para obtener el id del recurso
        500:
            description: Error al obtener el id del recurso
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.json

    # Llamar al servicio para obtener el id del recurso
    return services.get_id(body, username)

# Nuevo POST endpoint para obtener el id de un recurso por su nombre
@bp.route('/get_opts_id', methods=['POST'])
@fernetAuthenticate
def get_opts_id(username, isAdmin):
    """
    Obtener el id de un recurso por su nombre
    ---
    tags:
        - Api de administrador
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
    responses:
        200:
            description: Id del recurso obtenido exitosamente
        400:
            description: El recurso no existe
        401:
            description: No tiene permisos para obtener el id del recurso
        500:
            description: Error al obtener el id del recurso
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.json

    # Llamar al servicio para obtener el id del recurso
    return services.get_opts_id(body, username)

@bp.route('/create_type', methods=['POST'])
@fernetAuthenticate
def create_type(username, isAdmin):
    """
    Crear un nuevo tipo de contenido
    ---
    tags:
        - Api de administrador
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
                slug:
                    type: string
                description:
                    type: string
                metadata:
                    type: array
                    items:
                        type: object
                icon:
                    type: string
    responses:
        201:
            description: Tipo de contenido creado exitosamente
        400:
            description: El nombre o el slug del tipo de contenido está vacío
        401:
            description: No tiene permisos para crear un tipo de contenido
        500:
            description: Error al crear el tipo de contenido
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    # Obtener el body del request
    body = request.json

    # Llamar al servicio para crear el tipo de contenido
    return services.create_type(body, username)

@bp.route('/update_type', methods=['POST'])
@fernetAuthenticate
def update_type(username, isAdmin):
    """
    Actualizar un tipo de contenido
    ---
    tags:
        - Api de administrador
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                id:
                    type: string
                name:
                    type: string
                slug:
                    type: string
                description:
                    type: string
                metadata:
                    type: array
                    items:
                        type: object
                icon:
                    type: string
    responses:
        200:
            description: Tipo de contenido actualizado exitosamente
        401:
            description: No tiene permisos para actualizar un tipo de contenido
        404:
            description: El tipo de contenido no existe
        500:
            description: Error al actualizar el tipo de contenido
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    # Obtener el body del request
    body = request.json

    # Llamar al servicio para actualizar el tipo de contenido
    return services.update_type(body, username)

@bp.route('/get_type/<slug>', methods=['GET'])
@fernetAuthenticate
def get_type(username, isAdmin, slug):
    """
    Obtener el tipo de contenido por su slug
    ---
    tags:
        - Api de administrador
    parameters:
        - in: path
          name: slug
          schema:
            type: string
          required: true
          description: Slug del tipo de contenido
    responses:
        200:
            description: Tipo del recurso obtenido exitosamente
        401:
            description: No tiene permisos para obtener el tipo del recurso
        404:
            description: El tipo de contenido no existe
        500:
            description: Error al obtener el tipo del recurso
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    # Llamar al servicio para obtener el tipo del recurso
    return services.get_type(slug, username)