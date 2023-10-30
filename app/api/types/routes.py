from app.api.types import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.types import services
from app.api.users import services as user_services
from flask import request
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.utils.functions import cache_type_roles


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
    responses:
        200:
            description: Lista de tipos de contenido
        401:
            description: No tiene permisos para obtener los tipos de contenido
        500:
            description: Error al obtener los tipos de contenido
    """
    # Llamar al servicio para obtener todos los tipos de contenido
    return services.get_all()

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
                slug:
                    type: string
            required:
                - name
                - description
    responses:
        201:
            description: Tipo de contenido creado
        400:
            description: Error al crear el tipo de contenido
        401:
            description: No tiene permisos para crear un tipo de contenido
        500:
            description: Error al crear el tipo de contenido
    """
    # Obtener el body de la request
    body = request.json

    # Obtener el usuario actual
    current_user = get_jwt_identity()

    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': 'No tiene permisos para crear un tipo de contenido'}, 401
    
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
        slug_exists = services.get_by_slug(body['slug'])

        # Mientras el slug exista, agregar un número al final
        index = 1
        while 'msg' not in slug_exists:
            body['slug'] = body['slug'] + '-' + index
            slug_exists = services.get_by_slug(body['slug'])
            index += 1

        # Llamar al servicio para crear un tipo de contenido
        return services.create(body, current_user)
    else:
        slug_exists = services.get_by_slug(body['slug'])
        # si el service.get_by_slug devuelve un error, entonces el tipo de contenido no existe
        if 'msg' in slug_exists:
            if slug_exists['msg'] == 'Tipo de post no existe':
                # Llamar al servicio para crear un tipo de contenido
                return services.create(body, current_user)
        else:
            return {'msg': 'El slug ya existe'}, 400

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
    parameters:
        - slug (string): slug del tipo de contenido a obtener
    responses:
        200:
            description: Tipo de contenido
        401:
            description: No tiene permisos para obtener un tipo de contenido
        404:
            description: Tipo de contenido no existe
        500:
            description: Error al obtener el tipo de contenido
    """
    # se obtiene el usuario actual
    current_user = get_jwt_identity()
    # se verifica si el usuario tiene el rol de administrador o editor
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return {'msg': 'No tiene permisos para obtener un tipo de contenido'}, 401
    
    roles = cache_type_roles(slug)
    if roles['viewRoles']:
        canView = False
        for r in roles['viewRoles']:
            if user_services.has_role(current_user, r) or user_services.has_role(current_user, 'admin'):
                canView = True
                break
        if not canView:
            return {'msg': 'No tiene permisos para obtener un tipo de contenido'}, 401
            
    # Llamar al servicio para obtener un tipo de contenido por su slug
    slug_exists = services.get_by_slug(slug)
    # si el service.get_by_slug devuelve un error, entonces el tipo de contenido no existe
    if 'msg' in slug_exists:
        if slug_exists['msg'] == 'Tipo de contenido no existe':
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
            
                    
    responses:
        200:
            description: Tipo de contenido actualizado
        401:
            description: No tiene permisos para actualizar un tipo de contenido
        404:
            description: Tipo de contenido no existe
        500:
            description: Error al actualizar el tipo de contenido
    """
    # se obtiene el usuario actual
    current_user = get_jwt_identity()
    # se verifica si el usuario tiene el rol de administrador o editor
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return {'msg': 'No tiene permisos para actualizar un tipo de contenido'}, 401
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
    parameters:
        - slug (string): slug del tipo de contenido a eliminar
    responses:
        200:
            description: Tipo de contenido eliminado
        401:
            description: No tiene permisos para eliminar un tipo de contenido
        404:
            description: Tipo de contenido no existe
        500:
            description: Error al eliminar el tipo de contenido
    """
    # se obtiene el usuario actual
    current_user = get_jwt_identity()
    # se verifica si el usuario tiene el rol de administrador o editor
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return {'msg': 'No tiene permisos para eliminar un tipo de contenido'}, 401
    # Llamar al servicio para eliminar un tipo de contenido por su slug
    return services.delete_by_slug(slug, current_user)