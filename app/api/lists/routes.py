from app.api.lists import bp
from flask import jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.api.lists import services
from app.api.users import services as user_services
from flask_babel import _

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
    description: Requiere el rol admin o editor. Devuelve únicamente los campos name e id de cada listado.
    responses:
        200:
            description: Lista de listados obtenida exitosamente (solo name e id)
        401:
            description: No tienes permisos para realizar esta acción (no eres admin ni editor)
        500:
            description: Error al obtener los listados
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para obtener todos los listados
    resp = services.get_all()

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

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
    description: Requiere el rol admin o editor. Cada elemento de options se inserta como un documento independiente en la colección options; el listado guarda solo sus ids.
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
                options:
                    type: array
                    items:
                        type: object
                        properties:
                            term:
                                type: string
                        required:
                            - term
            required:
                - name
                - description
                - options
    responses:
        201:
            description: Listado creado exitosamente
        401:
            description: No tienes permisos para realizar esta acción (no eres admin ni editor)
        500:
            description: Error al crear el listado (incluye body malformado, p. ej. sin options)
    """
    # Obtener el body de la request
    body = request.json

    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    
    # Llamar al servicio para crear un listado nuevo
    return services.create(body, current_user)

# Nuevo endpoint para devolver un estándar por su slug
@bp.route('/<id>', methods=['GET'])
@jwt_required()
def get_by_id(id):
    """
    Obtener un listado por su id
    ---
    security:
        - JWT: []
    tags:
        - Listados
    description: Requiere el rol admin o editor. Devuelve name, description y options (cada option resuelta a {id, term}).
    parameters:
        - in: path
          name: id
          type: string
          required: true
    responses:
        200:
            description: >
              Listado obtenido exitosamente. Nota - si el id no existe o es inválido,
              esta ruta también responde 200 con un mensaje de error en el cuerpo en
              lugar de 404, debido a un manejo de excepciones que no propaga el
              código de estado.
        401:
            description: No tienes permisos para realizar esta acción (no eres admin ni editor)
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg':  _('You don\'t have the required authorization')}), 401
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
    description: >
      Requiere el rol admin o editor. El body debe incluir options; cada elemento
      existente (con id) se actualiza, cada elemento nuevo se inserta, y los
      marcados con deleted=true se excluyen del listado resultante. Si options se
      omite del body, la ruta no realiza ningún cambio ni devuelve respuesta
      (falla con error 500 de Flask).
    parameters:
        - in: path
          name: id
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
                        properties:
                            id:
                                type: string
                                description: Presente para actualizar una opción existente; ausente para crear una nueva.
                            term:
                                type: string
                            deleted:
                                type: boolean
                                description: Si es true, la opción se elimina del listado.
            required:
                - options
    responses:
        200:
            description: Listado actualizado exitosamente
        400:
            description: El body no es un JSON válido
        401:
            description: No tienes permisos para realizar esta acción (no eres admin ni editor)
        404:
            description: Listado no encontrado
        500:
            description: Error al actualizar el listado (incluye el caso de un body sin la clave options)
    """
    
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # parseamos el body
    try:
        body = request.json
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 400
    # Llamar al servicio para actualizar el estándar por su slug
    return services.update_by_id(id, body, current_user)

# Nuevo endpoint para eliminar un listado por su slug
@bp.route('/<id>', methods=['DELETE'])
@jwt_required()
def delete_by_id(id):
    """
    Eliminar un listado por su id
    ---
    security:
        - JWT: []
    tags:
        - Listados
    description: Requiere el rol admin o editor. No se valida si el listado está en uso por algún formulario o campo antes de eliminarlo.
    parameters:
        - in: path
          name: id
          schema:
            type: string
          required: true
    responses:
        200:
            description: Listado eliminado exitosamente
        401:
            description: No tienes permisos para realizar esta acción (no eres admin ni editor)
        404:
            description: Listado no encontrado
        500:
            description: Error al eliminar el listado
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para eliminar el listado por su slug
    return services.delete_by_id(id, current_user)