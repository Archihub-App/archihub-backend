from app.api.adminApi import bp
from flask import jsonify
from flask import request
from app.api.adminApi import services
from app.utils.FernetAuth import fernetAuthenticate
import json
from flask_babel import _

@bp.route('/get_system_info', methods=['GET'])
@fernetAuthenticate
def get_info(username, isAdmin):
    """
    Obtener información general del sistema (tipos de contenido, capacidades activas y métricas)
    ---
    security:
        - JWT: []
    tags:
        - Api de administrador
    description: >
        Requiere un token de administrador emitido por el flujo de tokens de la API
        (token Fernet cifrado enviado en el header `Authorization: Bearer <token>`,
        distinto del JWT normal de `/auth/login`). El usuario debe tener el rol `admin`.
    responses:
        200:
            description: Información del sistema obtenida exitosamente (post_types, capabilities, metrics)
        401:
            description: Token no provisto, inválido, expirado, o el usuario no tiene el rol admin
        500:
            description: Error al obtener la información del sistema
    """
    if not isAdmin:
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    return services.get_system_info(username)

# Nuevo POST endpoint para crear nuevos recursos
@bp.route('/create', methods=['POST'])
@fernetAuthenticate
def new_resource(username, isAdmin):
    """
    Crear un nuevo recurso (delega en app.api.resources.services.create tras autocompletar campos por defecto)
    ---
    security:
        - JWT: []
    tags:
        - Api de administrador
    consumes:
        - multipart/form-data
    description: >
        Requiere token de administrador (ver `/get_system_info`). El cuerpo NO es JSON: es
        `multipart/form-data` con un campo `data` que contiene el JSON serializado del recurso
        y, opcionalmente, uno o más archivos bajo el campo `files`. Si `post_type`, `status`,
        `parent`, `parents`, `filesIds` o `updateCache` no vienen en `data`, se autocompletan
        con valores por defecto antes de reenviar la petición al servicio de recursos.
    parameters:
        - in: formData
          name: data
          type: string
          required: true
          description: 'JSON serializado, p.ej. {"metadata": {...}, "post_type": "...", "status": "published"}'
        - in: formData
          name: files
          type: file
          required: false
          description: Uno o más archivos a asociar al recurso (puede repetirse)
    responses:
        201:
            description: Recurso creado exitosamente
        400:
            description: El recurso no tiene la metadata requerida por su tipo de contenido
        401:
            description: Token no provisto/inválido o el usuario no tiene el rol admin
        500:
            description: Error al crear el recurso (p.ej. falta el campo "data" en el form)
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
    Actualizar un recurso existente (delega en app.api.resources.services.update_by_id)
    ---
    security:
        - JWT: []
    tags:
        - Api de administrador
    consumes:
        - multipart/form-data
    description: >
        Requiere token de administrador. Igual que `/create`, el cuerpo es `multipart/form-data`,
        no JSON: un campo `id` con el id del recurso, un campo `data` con el JSON serializado de
        los cambios, y opcionalmente archivos nuevos bajo `files`. `deletedFiles` se autocompleta
        a `[]` en `data` si no viene.
    parameters:
        - in: formData
          name: id
          type: string
          required: true
          description: Id del recurso a actualizar (KeyError/500 si falta)
        - in: formData
          name: data
          type: string
          required: true
          description: JSON serializado con los campos a actualizar
        - in: formData
          name: files
          type: file
          required: false
          description: Archivos nuevos a asociar (puede repetirse)
    responses:
        200:
            description: Recurso actualizado exitosamente
        401:
            description: Token no provisto/inválido o el usuario no tiene el rol admin
        500:
            description: Error al actualizar el recurso (p.ej. falta "id" o "data" en el form)
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
    Obtener el id (y campos básicos) de un único recurso publicado que cumpla un filtro
    ---
    security:
        - JWT: []
    tags:
        - Api de administrador
    description: >
        El cuerpo se usa TAL CUAL como filtro de MongoDB sobre la colección `resources`
        (se le agrega automáticamente `status: "published"`), por lo que puede incluir
        cualquier campo indexado del recurso (p.ej. una ruta de metadata), no solo `name`.
        Devuelve el primer recurso que coincida.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            description: 'Filtro MongoDB arbitrario, p.ej. {"metadata.firstLevel.title": "..."}'
    responses:
        200:
            description: Recurso encontrado (id, post_type, metadata, filesObj, parent, parents)
        401:
            description: Token no provisto/inválido o el usuario no tiene el rol admin
        404:
            description: No se encontró ningún recurso publicado que cumpla el filtro
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
    Obtener el id de una opción de lista (colección "options") por su término exacto
    ---
    security:
        - JWT: []
    tags:
        - Api de administrador
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                term:
                    type: string
            required:
                - term
    responses:
        200:
            description: Id de la opción obtenido exitosamente
        401:
            description: Token no provisto/inválido o el usuario no tiene el rol admin
        404:
            description: No existe ninguna opción con ese término
        500:
            description: Error al obtener el id de la opción (p.ej. falta "term" en el body)
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
    Crear un nuevo tipo de contenido (delega en app.api.types.services.create)
    ---
    security:
        - JWT: []
    tags:
        - Api de administrador
    description: Requiere token de administrador. El body se reenvía tal cual a app.api.types.services.create.
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
            required:
                - name
                - slug
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
    Actualizar un tipo de contenido (delega en app.api.types.services.update_by_slug)
    ---
    security:
        - JWT: []
    tags:
        - Api de administrador
    description: >
        Requiere token de administrador. `slug` identifica el tipo a actualizar y es
        obligatorio (se lee directamente de `body['slug']`; su ausencia produce un error 500,
        no un 400). No existe un campo `id` para este endpoint.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                slug:
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
            required:
                - slug
    responses:
        200:
            description: Tipo de contenido actualizado exitosamente
        401:
            description: Token no provisto/inválido o el usuario no tiene el rol admin
        404:
            description: El tipo de contenido no existe
        500:
            description: Error al actualizar el tipo de contenido (p.ej. falta "slug" en el body)
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
    Obtener el tipo de contenido por su slug (delega en app.api.types.services.get_by_slug)
    ---
    security:
        - JWT: []
    tags:
        - Api de administrador
    description: Requiere token de administrador.
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