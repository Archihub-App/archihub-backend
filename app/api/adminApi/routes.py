from app.api.adminApi import bp
from flask import jsonify
from flask import request
from app.api.adminApi import services
from app.utils.FernetAuth import fernetAuthenticate

# Nuevo POST endpoint para crear nuevos recursos
@bp.route('/create', methods=['POST'])
@fernetAuthenticate
def new_resource(username, isAdmin):
    """
    Crear un nuevo recurso
    ---
    tags:
        - Recursos
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
        200:
            description: Recurso creado exitosamente
        401:
            description: No tiene permisos para crear un recurso
        500:
            description: Error al crear el recurso
    """
    if not isAdmin:
        return jsonify({'msg': 'No tiene permisos para crear un recurso'}), 401
    # Obtener el body del request
    body = request.json

    # Llamar al servicio para crear el recurso
    return services.create(body, username)

# Nuevo POST endpoint para obtener el id de un recurso por su nombre
@bp.route('/get_id', methods=['POST'])
@fernetAuthenticate
def get_resource_id(username, isAdmin):
    """
    Obtener el id de un recurso por su nombre
    ---
    tags:
        - Recursos
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
        401:
            description: No tiene permisos para obtener el id del recurso
        500:
            description: Error al obtener el id del recurso
    """
    if not isAdmin:
        return jsonify({'msg': 'No tiene permisos para obtener el id del recurso'}), 401
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
        - Recursos
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
        401:
            description: No tiene permisos para obtener el id del recurso
        500:
            description: Error al obtener el id del recurso
    """
    if not isAdmin:
        return jsonify({'msg': 'No tiene permisos para obtener el id del recurso'}), 401
    # Obtener el body del request
    body = request.json

    # Llamar al servicio para obtener el id del recurso
    return services.get_opts_id(body, username)