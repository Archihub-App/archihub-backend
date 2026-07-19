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
    Get all lists from the database
    ---
    security:
        - JWT: []
    tags:
        - Lists
    description: Requires the admin or editor role. Returns only the name and id fields of each list.
    responses:
        200:
            description: List of lists retrieved successfully (name and id only)
        401:
            description: You don't have permission to perform this action (not admin or editor)
        500:
            description: Error retrieving the lists
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
    Create a new list with the request body
    ---
    security:
        - JWT: []
    tags:
        - Lists
    description: Requires the admin or editor role. Each element of options is inserted as an independent document in the options collection; the list stores only their ids.
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
            description: List created successfully
        401:
            description: You don't have permission to perform this action (not admin or editor)
        500:
            description: Error creating the list (includes a malformed body, e.g. missing options)
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
    Get a list by its id
    ---
    security:
        - JWT: []
    tags:
        - Lists
    description: Requires the admin or editor role. Returns name, description, and options (each option resolved to {id, term}).
    parameters:
        - in: path
          name: id
          type: string
          required: true
    responses:
        200:
            description: >
              List retrieved successfully. Note - if the id does not exist or is invalid,
              this route also responds 200 with an error message in the body instead
              of 404, due to exception handling that does not propagate the
              status code.
        401:
            description: You don't have permission to perform this action (not admin or editor)
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
    Update a list by its id
    ---
    security:
        - JWT: []
    tags:
        - Lists
    description: >
      Requires the admin or editor role. The body must include options; each existing
      element (with id) is updated, each new element is inserted, and those
      marked deleted=true are excluded from the resulting list. If options is
      omitted from the body, the route makes no change and returns no response
      (fails with a Flask 500 error).
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
                                description: Present to update an existing option; absent to create a new one.
                            term:
                                type: string
                            deleted:
                                type: boolean
                                description: If true, the option is removed from the list.
            required:
                - options
    responses:
        200:
            description: List updated successfully
        400:
            description: The body is not valid JSON
        401:
            description: You don't have permission to perform this action (not admin or editor)
        404:
            description: List not found
        500:
            description: Error updating the list (includes the case of a body missing the options key)
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
    Delete a list by its id
    ---
    security:
        - JWT: []
    tags:
        - Lists
    description: Requires the admin or editor role. It is not validated whether the list is in use by any form or field before deleting it.
    parameters:
        - in: path
          name: id
          schema:
            type: string
          required: true
    responses:
        200:
            description: List deleted successfully
        401:
            description: You don't have permission to perform this action (not admin or editor)
        404:
            description: List not found
        500:
            description: Error deleting the list
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Si el usuario no es admin, retornar error
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para eliminar el listado por su slug
    return services.delete_by_id(id, current_user)