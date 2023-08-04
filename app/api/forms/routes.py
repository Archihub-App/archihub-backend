from app.api.forms import bp
from flask import jsonify, request
from flask_jwt_extended import jwt_required
from app.api.forms import services
from flask_jwt_extended import get_jwt_identity

# En este archivo se registran las rutas de la API para los estándares de metadatos

# Nuevo endpoint para obtener todos los estándares de metadatos
@bp.route('', methods=['GET'])
@jwt_required()
def get_all():
    """
    Obtener todos los estándares de metadatos de la base de datos
    ---
    security:
        - JWT: []
    tags:
        - Estándares de metadatos
    responses:
        200:
            description: Lista de estándares de metadatos
    """
    # Llamar al servicio para obtener todos los estándares de metadatos
    return services.get_all()

# Nuevo endpoint para crear un estándar de metadatos
@bp.route('', methods=['POST'])
@jwt_required()
def create():
    """
    Crear un estándar de metadatos nuevo con el body del request
    ---
    security:
        - JWT: []
    tags:
        - Estándares de metadatos
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
                fields:
                    type: array
                    items:
                        type: object
            required:
                - name
                - description
    responses:
        201:
            description: Estándar de metadatos creado exitosamente
        400:
            description: Error al crear el estándar de metadatos
        401:
            description: No tienes permisos para realizar esta acción
    """
    # Obtener el body de la request
    body = request.json
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not services.is_admin(current_user):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # si el slug no está definido, crearlo
    if 'slug' not in body or body['slug'] == '':
        body['slug'] = body['name'].lower().replace(' ', '-')
        # quitamos los caracteres especiales y las tildes pero dejamos los guiones
        body['slug'] = ''.join(e for e in body['slug'] if e.isalnum() or e == '-')
        # quitamos los guiones al inicio y al final
        body['slug'] = body['slug'].strip('-')
        # quitamos los guiones repetidos
        body['slug'] = body['slug'].replace('--', '-')

        # llamamos al servicio para verificar si el slug ya existe
        slug_exists = services.slug_exists(body['slug'])
        # Mientras el slug exista, agregar un número al final
        index = 1
        while 'msg' not in slug_exists:
            body['slug'] = body['slug'] + '-' + index
            slug_exists = services.get_by_slug(body['slug'])
            index += 1
            
        # Llamar al servicio para crear un estándar de metadatos
        return services.create(body)
    else:
        slug_exists = services.get_by_slug(body['slug'])
        # si el service.get_by_slug devuelve un error, entonces el tipo de contenido no existe
        if 'msg' in slug_exists:
            if slug_exists['msg'] == 'Tipo de contenido no existe':
                # Llamar al servicio para crear un tipo de contenido
                return services.create(body)
        else:
            return {'msg': 'El slug ya existe'}, 400

# Nuevo endpoint para devolver un estándar por su slug
@bp.route('/<slug>', methods=['GET'])
@jwt_required()
def get_by_slug(slug):
    """
    Obtener un estándar por su slug
    ---
    security:
        - JWT: []
    tags:
        - Estándares de metadatos
    parameters:
        - in: path
          name: slug
          type: string
          required: true
    responses:
        200:
            description: estándar obtenido exitosamente
        404:
            description: estándar no encontrado
    """
    # Llamar al servicio para obtener el estándar por su slug
    resp = services.get_by_slug(slug)
    # Si el estándar no existe, retornar error
    if 'msg' in resp:
        if resp['msg'] == 'estándar no existe':
            return jsonify(resp), 404
    # Retornar el estándar
    return jsonify(resp), 200