from app.api.types import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.types import services
from app.api.users import services as user_services
from flask import request
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.utils.functions import cache_type_roles
from app.utils import DatabaseHandler
from flask_babel import _

mongodb = DatabaseHandler.DatabaseHandler()

# En este archivo se registran las rutas de la API para los tipos de contenido

# Nuevo endpoint para obtener todos los tipos de contenido
@bp.route('', methods=['GET'])
@jwt_required()
def get_all():
    """
    Obtener todos los tipos de contenido de catalogación
    ---
    security:
        - JWT: []
    tags:
        - Tipos de contenido
    description: No hay restricción de rol adicional más allá de tener sesión válida (401 solo si falta o es inválido el token JWT). Devuelve únicamente name, description y slug de cada tipo.
    responses:
        200:
            description: Lista de tipos de contenido (solo name, description, slug)
        401:
            description: Token JWT ausente o inválido
        500:
            description: Error al obtener los tipos de contenido
    """
    # Llamar al servicio para obtener todos los tipos de contenido
    resp = services.get_all()
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

# Nuevo endpoint para crear un tipo de contenido
@bp.route('', methods=['POST'])
@jwt_required()
def create():
    """
    Crear un tipo de contenido nuevo con el body del request
    ---
    security:
        - JWT: []
    tags:
        - Tipos de contenido
    description: Requiere el rol admin. slug es obligatorio como clave del body (puede ir vacío para autogenerarlo desde name); si se envía un slug no vacío que ya existe, la creación falla con 400.
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
                slug:
                    type: string
                    description: Clave obligatoria; enviar "" para autogenerar el slug desde name.
                metadata:
                    type: string
                    description: Slug del formulario (forms) asociado como metadato del tipo.
                icon:
                    type: string
                hierarchical:
                    type: boolean
                parentType:
                    type: array
                    items:
                        type: object
                isArticle:
                    type: boolean
            required:
                - name
                - description
                - slug
    responses:
        201:
            description: Tipo de contenido creado
        400:
            description: El slug ya existe, o name/slug están vacíos
        401:
            description: No tiene permisos para crear un tipo de contenido (no eres admin)
        500:
            description: Error al crear el tipo de contenido
    """

    # Obtener el body de la request
    body = request.json

    # Obtener el usuario actual
    current_user = get_jwt_identity()

    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    
    # Si el slug no está definido, crearlo
    if not body['slug'] or body['slug'] == '':
        body['slug'] = body['name'].lower().replace(' ', '-')
        # quitamos los caracteres especiales y las tildes pero dejamos los guiones
        body['slug'] = ''.join(e for e in body['slug'] if e.isalnum() or e == '-')
        # quitamos los guiones al inicio y al final
        body['slug'] = body['slug'].strip('-')
        # quitamos los guiones repetidos
        body['slug'] = body['slug'].replace('--', '-')

        # Llamar al servicio para obtener un tipo de contenido por su slug
        slug_exists = mongodb.get_record('post_types', {'slug': body['slug']}, {'slug': 1})
        
        # Mientras el slug exista, agregar un número al final
        index = 1
        begin_slug = body['slug']
        while slug_exists:
            body['slug'] = begin_slug + '-' + str(index)
            slug_exists = mongodb.get_record('post_types', {'slug': body['slug']}, {'slug': 1})
            index += 1

        # Llamar al servicio para crear un tipo de contenido
        return services.create(body, current_user)
    else:
        slug_exists = mongodb.get_record('post_types', {'slug': body['slug']}, {'slug': 1})
        if not slug_exists:
            return services.create(body, current_user)
        else:
            return {'msg': _('Slug already exists')}, 400

# Nuevo endpoint para obtener un tipo de contenido por su slug
@bp.route('/<slug>', methods=['GET'])
@jwt_required()
def get_by_slug(slug):
    """
    Obtener un tipo de contenido por su slug
    ---
    security:
        - JWT: []
    tags:
        - Tipos de contenido
    description: Requiere el rol admin o editor. Si el tipo tiene viewRoles configurado, además se exige que el usuario tenga admin o alguno de esos roles. Incluye en la respuesta parentsTypes (jerarquía de padres) y, si tiene metadata asociada, el formulario resuelto.
    parameters:
        - in: path
          name: slug
          type: string
          required: true
    responses:
        200:
            description: Tipo de contenido
        401:
            description: No tiene permisos para obtener este tipo de contenido
        404:
            description: Tipo de contenido no existe
        500:
            description: Error al obtener el tipo de contenido (incluye errores al resolver el formulario de metadata)
    """
    # se obtiene el usuario actual
    current_user = get_jwt_identity()
    # se verifica si el usuario tiene el rol de administrador o editor
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    
    roles = cache_type_roles(slug)
    if roles['viewRoles']:
        canView = False
        for r in roles['viewRoles']:
            if user_services.has_role(current_user, r) or user_services.has_role(current_user, 'admin'):
                canView = True
                break
        if not canView:
            return {'msg': _('You don\'t have the required authorization')}, 401
            
    # Llamar al servicio para obtener un tipo de contenido por su slug
    slug_exists = services.get_by_slug(slug)
    # si el service.get_by_slug devuelve un error, entonces el tipo de contenido no existe
    if 'msg' in slug_exists:
        if slug_exists['msg'] == _('Type not found'):
            return slug_exists, 404
    else:
        return slug_exists

# Nuevo endpoint para actualizar un tipo de contenido por su slug
@bp.route('/<slug>', methods=['PUT'])
@jwt_required()
def update_by_slug(slug):
    """
    Actualizar un tipo de contenido por su slug
    ---
    security:
        - JWT: []
    tags:
        - Tipos de contenido
    description: Requiere el rol admin o editor. parentType debe enviarse siempre (aunque sea una lista vacía); si se omite del body, la petición falla con 500 por un KeyError interno. Si parentType incluye el propio slug como padre, se elimina automáticamente de la lista para evitar un ciclo directo. editRoles/viewRoles se validan contra los roles existentes.
    parameters:
        - in: path
          name: slug
          schema:
            type: string
          required: true
          description: slug del tipo de contenido a actualizar
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
                description:
                    type: string
                icon:
                    type: string
                hierarchical:
                    type: boolean
                parentType:
                    type: array
                    items:
                        type: object
                metadata:
                    type: string
                editRoles:
                    type: array
                    items:
                        type: string
                viewRoles:
                    type: array
                    items:
                        type: string
                isArticle:
                    type: boolean
    responses:
        200:
            description: Tipo de contenido actualizado
        401:
            description: No tiene permisos para actualizar un tipo de contenido (no eres admin ni editor)
        404:
            description: Tipo de contenido no existe
        500:
            description: Error al actualizar el tipo de contenido (incluye roles inexistentes en editRoles/viewRoles)
    """
    # se obtiene el usuario actual
    current_user = get_jwt_identity()
    # se verifica si el usuario tiene el rol de administrador o editor
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Obtener el body de la request
    body = request.json
    # Llamar al servicio para actualizar un tipo de contenido por su slug
    return services.update_by_slug(slug, body, current_user)
            

# Nuevo endpoint para eliminar un tipo de contenido por su slug
@bp.route('/<slug>', methods=['DELETE'])
@jwt_required()
def delete_by_slug(slug):
    """
    Eliminar un tipo de contenido por su slug
    ---
    security:
        - JWT: []
    tags:
        - Tipos de contenido
    description: Requiere el rol admin o editor. No elimina físicamente los recursos asociados; los marca con status "deleted".
    parameters:
        - in: path
          name: slug
          schema:
            type: string
          required: true
          description: slug del tipo de contenido a eliminar
    responses:
        200:
            description: Tipo de contenido eliminado (y sus recursos marcados como eliminados)
        401:
            description: No tiene permisos para eliminar un tipo de contenido (no eres admin ni editor)
        404:
            description: Tipo de contenido no existe
        500:
            description: Error al eliminar el tipo de contenido
    """
    # se obtiene el usuario actual
    current_user = get_jwt_identity()
    # se verifica si el usuario tiene el rol de administrador o editor
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Llamar al servicio para eliminar un tipo de contenido por su slug
    resp = services.delete_by_slug(slug, current_user)
    if isinstance(resp, dict):
        return resp
    else:
        return tuple(resp)
    
@bp.route('/moreinfo', methods=['POST'])
@jwt_required()
def get_type_viz():
    """
    Obtener estadísticas de visualización (gráficas) de un tipo de contenido
    ---
    security:
        - JWT: []
    tags:
        - Tipos de contenido
    description: >
      Requiere el rol admin o editor. type acepta timeCreated (conteo por fecha de
      creación), statusCount (conteo por status) o authorCount (top 10 por
      createdBy); cualquier otro valor de type devuelve un mensaje ok sin datos.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                slug:
                    type: string
                    description: slug del tipo de contenido
                type:
                    type: string
                    enum: [timeCreated, statusCount, authorCount]
            required:
                - slug
                - type
    responses:
        200:
            description: Datos agregados para la gráfica solicitada
        400:
            description: Faltan slug o type en el body
        401:
            description: No tiene permisos para esta acción (no eres admin ni editor)
        500:
            description: Error al obtener la información
    """
    body = request.get_json()

    if 'slug' not in body or 'type' not in body:
        return {'msg': _('You must specify the slug and the type')}, 400
    
    if not user_services.has_role(get_jwt_identity(), 'admin') and not user_services.has_role(get_jwt_identity(), 'editor'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    
    resp = services.get_type_viz(body['slug'], body['type'])

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp