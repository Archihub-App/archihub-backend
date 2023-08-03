from app.api.types import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.types import services
from app.api.users import services as user_services
from flask import request

# En este archivo se registran las rutas de la API para los tipos de post

# Nuevo endpoint para obtener todos los tipos de post
@bp.route('', methods=['GET'])
@jwt_required()
def get_all():
    """
    Obtener todos los tipos de post
    ---
    security:
        - JWT: []
    tags:
        - Tipos de post
    responses:
        200:
            description: Lista de tipos de post
    """
    # Llamar al servicio para obtener todos los tipos de post
    return services.get_all()

# Nuevo endpoint para crear un tipo de post
@bp.route('', methods=['POST'])
@jwt_required()
def create():
    """
    Crear un tipo de post nuevo con el body del request
    ---
    security:
        - JWT: []
    tags:
        - Tipos de post
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
            description: Tipo de post creado
        400:
            description: Error al crear el tipo de post
    """
    # Obtener el body de la request
    body = request.json

    # Obtener el usuario actual
    current_user = get_jwt_identity()

    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': 'No tiene permisos para crear un tipo de post'}, 401
    
    # Si el slug no está definido, crearlo
    if not body['slug'] or body['slug'] == '':
        body['slug'] = body['name'].lower().replace(' ', '-')
        # quitamos los caracteres especiales y las tildes pero dejamos los guiones
        body['slug'] = ''.join(e for e in body['slug'] if e.isalnum() or e == '-')

    slug_exists = services.get_by_slug(body['slug'])
    # si el service.get_by_slug devuelve un error, entonces el tipo de post no existe
    if 'msg' in slug_exists:
        if slug_exists['msg'] == 'Tipo de post no existe':
            # Llamar al servicio para crear un tipo de post
            return services.create(body)
    else:
        return {'msg': 'El slug ya existe'}, 400

# Nuevo endpoint para obtener un tipo de post por su slug
@bp.route('/<slug>', methods=['GET'])
@jwt_required()
def get_by_slug(slug):
    """
    Obtener un tipo de post por su slug
    ---
    security:
        - JWT: []
    tags:
        - Tipos de post
    parameters:
        - in: path
            name: slug
            schema:
                type: string
            required: true
    responses:
        200:
            description: Tipo de post
        404:
            description: Tipo de post no existe
    """
    # se obtiene el usuario actual
    current_user = get_jwt_identity()
    # se verifica si el usuario tiene el rol de administrador o catalogador_gestor
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'catalogador_gestor'):
        return {'msg': 'No tiene permisos para obtener un tipo de post'}, 401
    # Llamar al servicio para obtener un tipo de post por su slug
    slug_exists = services.get_by_slug(slug)
    # si el service.get_by_slug devuelve un error, entonces el tipo de post no existe
    if 'msg' in slug_exists:
        if slug_exists['msg'] == 'Tipo de post no existe':
            return slug_exists, 404
    else:
        return slug_exists

# Nuevo endpoint para actualizar un tipo de post por su slug
@bp.route('/<slug>', methods=['PUT'])
@jwt_required()
def update_by_slug(slug):
    """
    Actualizar un tipo de post por su slug
    ---
    security:
        - JWT: []
    tags:
        - Tipos de post
    parameters:
        - in: path
            name: slug
            schema:
                type: string
            required: true
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
                required:
                    - name
                    - description
    responses:
        200:
            description: Tipo de post actualizado
        404:
            description: Tipo de post no existe
    """
    # se obtiene el usuario actual
    current_user = get_jwt_identity()
    # se verifica si el usuario tiene el rol de administrador o catalogador_gestor
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'catalogador_gestor'):
        return {'msg': 'No tiene permisos para actualizar un tipo de post'}, 401
    # Obtener el body de la request
    body = request.json
    # Llamar al servicio para actualizar un tipo de post por su slug
    return services.update_by_slug(slug, body)
            