from app.api.lists import bp
from flask import jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.api.lists import services
from app.api.users import services as user_services

# En este archivo se registran las rutas de la API para los listados cerrados

# Nuevo endpoint para obtener todos los listados
@bp.route('', methods=['GET'])
@jwt_required()
def get_all():
    """
    Obtener todos los listados de la base de datos
    ---
    security:
        - JWT: []
    tags:
        - Listados
    responses:
        200:
            description: Lista de listados
        401:
            description: No tienes permisos para realizar esta acción
        500:
            description: Error al obtener los listados
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Llamar al servicio para obtener todos los listados
    return services.get_all()

# Nuevo endpoint para crear un listado
@bp.route('', methods=['POST'])
@jwt_required()
def create():
    """
    Crear un listado nuevo con el body del request
    ---
    security:
        - JWT: []
    tags:
        - Listados
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
            description: Listado creado exitosamente
        400:
            description: Error al crear el listado
        401:
            description: No tienes permisos para realizar esta acción
        500:
            description: Error al crear el listado
    """
    # Obtener el body de la request
    body = request.json
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
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
        slug_exists = services.get_by_slug(body['slug'])
        # Mientras el slug exista, agregar un número al final
        index = 1
        while 'msg' not in slug_exists:
            body['slug'] = body['slug'] + '-' + index
            slug_exists = services.get_by_slug(body['slug'])
            index += 1
            
        # Llamar al servicio para crear un listado
        return services.create(body, current_user)
    else:
        slug_exists = services.get_by_slug(body['slug'])
        # si el service.get_by_slug devuelve un error, entonces el listado no existe
        if 'msg' in slug_exists:
            if slug_exists['msg'] == 'Listado no existe':
                # Llamar al servicio para crear un listado
                return services.create(body, current_user)
        else:
            return {'msg': 'El slug ya existe'}, 400

# Nuevo endpoint para devolver un estándar por su slug
@bp.route('/<id>', methods=['GET'])
@jwt_required()
def get_by_id(id):
    """
    Obtener un estándar por su id
    ---
    security:
        - JWT: []
    tags:
        - Listados
    parameters:
        - in: path
          name: id
          type: string
          required: true
    responses:
        200:
            description: Listado obtenido exitosamente
        401:
            description: No tienes permisos para realizar esta acción
        404:
            description: Listado no encontrado
        500:
            description: Error al obtener el listado
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Llamar al servicio para obtener el listado por su id
    resp = services.get_by_id(id)
    # Si el listado no existe, retornar error
    if 'msg' in resp:
        if resp['msg'] == 'Listado no existe':
            return jsonify(resp), 404
    # Retornar el listado
    return jsonify(resp), 200

# Nuevo endpoint para actualizar un listado por su slug
@bp.route('/<id>', methods=['PUT'])
@jwt_required()
def update_by_id(id):
    """
    Actualizar un listado por su id
    ---
    security:
        - JWT: []
    tags:
        - Listados
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
                options:
                    type: array
                    items:
                        type: object

            required:
                - name
    responses:
        200:
            description: Listado actualizado exitosamente
        400:
            description: Error al actualizar el listado
        401:
            description: No tienes permisos para realizar esta acción
        404:
            description: Listado no encontrado
        500:
            description: Error al actualizar el listado
    """
    
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # parseamos el body
    try:
        body = request.json
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 400
    print("hola")
    # Llamar al servicio para actualizar el estándar por su slug
    return services.update_by_id(id, body, current_user)

# Nuevo endpoint para eliminar un listado por su slug
@bp.route('/<slug>', methods=['DELETE'])
@jwt_required()
def delete_by_slug(slug):
    """
    Eliminar un listado por su slug
    ---
    security:
        - JWT: []
    tags:
        - Listados
    parameters:
        - in: path
          name: slug
          schema:
            type: string
          required: true
    responses:
        200:
            description: Listado eliminado exitosamente
        401:
            description: No tienes permisos para realizar esta acción
        404:
            description: Listado no encontrado
        500:
            description: Error al eliminar el listado
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin'):
        return jsonify({'msg': 'No tienes permisos para realizar esta acción'}), 401
    # Llamar al servicio para eliminar el listado por su slug
    return services.delete_by_slug(slug, current_user)