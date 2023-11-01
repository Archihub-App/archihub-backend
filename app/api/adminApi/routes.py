from app.api.adminApi import bp
from flask import jsonify
from flask import request
from app.api.adminApi import services
from app.utils.FernetAuth import fernetAuthenticate

# Nuevo POST endpoint para crear nuevos recursos
@bp.route('/create', methods=['POST'])
@fernetAuthenticate
def new_resource(username):
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
    # Obtener el body del request
    body = request.json

    # Llamar al servicio para crear el recurso
    return services.create(body, username)