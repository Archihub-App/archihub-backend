from app.api.views import bp
from flask_jwt_extended import jwt_required, get_jwt_identity
from flask import jsonify, request
from app.api.views import services
from app.api.users import services as user_services
from flask_babel import _
import json

@bp.route('/<view_id>', methods=['GET'])
@jwt_required()
def get_view(view_id):
    """
    Obtener una vista de consulta por su id (incluye su thumbnail en base64 si tiene uno)
    ---
    security:
        - JWT: []
    tags:
        - Vistas
    description: Requiere el rol "admin" o "editor".
    parameters:
        - in: path
          name: view_id
          type: string
          required: true
          description: ObjectId de MongoDB de la vista
    responses:
        200:
            description: Retorna la vista de consulta
        401:
            description: No tienes el rol admin/editor requerido
        404:
            description: La vista no existe
        500:
            description: Error interno no manejado explícitamente (p.ej. view_id con formato de ObjectId inválido)
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para obtener una vista de consulta
    resp = services.get(view_id, current_user)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp

@bp.route('/<view_id>', methods=['PUT'])
@jwt_required()
def update_view(view_id):
    """
    Actualizar una vista de consulta (nombre, descripción, tipos visibles, thumbnail, etc.)
    ---
    security:
        - JWT: []
    tags:
        - Vistas
    consumes:
        - multipart/form-data
    description: >
        Requiere el rol "admin" o "editor". El cuerpo es `multipart/form-data`, no JSON: un
        campo `data` con el JSON serializado de los campos a actualizar (ver ViewUpdate:
        name, description, parent, root, visible, defaultView, slug — todos opcionales) y,
        opcionalmente, UN único archivo de imagen bajo `files` que reemplaza el thumbnail
        (se elimina el archivo anterior asociado a la vista).
    parameters:
        - in: path
          name: view_id
          type: string
          required: true
          description: ObjectId de MongoDB de la vista
        - in: formData
          name: data
          type: string
          required: true
          description: JSON serializado con los campos a actualizar
        - in: formData
          name: files
          type: file
          required: false
          description: A lo sumo un archivo de imagen (jpg/jpeg/png/gif/tif/tiff/heic/bmp/webp)
    responses:
        200:
            description: Vista de consulta actualizada exitosamente
        400:
            description: Se envió más de un archivo, o el archivo no es una imagen soportada
        401:
            description: No tienes el rol admin/editor requerido
        404:
            description: La vista no existe
        500:
            description: Error al actualizar la vista de consulta (p.ej. falta "data" en el form, o falla el procesamiento de la imagen)
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.form.to_dict()
    data = body.get('data')
    data = json.loads(data)
    
    files = request.files.getlist('files')
    
    # Llamar al servicio para actualizar una vista de consulta
    return services.update(view_id, data, current_user, files)

@bp.route('/<view_id>', methods=['DELETE'])
@jwt_required()
def delete_view(view_id):
    """
    Eliminar una vista de consulta (y su thumbnail asociado, si tiene uno)
    ---
    security:
        - JWT: []
    tags:
        - Vistas
    description: Requiere el rol "admin" o "editor".
    parameters:
        - in: path
          name: view_id
          type: string
          required: true
          description: ObjectId de MongoDB de la vista
    responses:
        200:
            description: Vista de consulta eliminada exitosamente (también si el id no existía — no hay 404 explícito)
        401:
            description: No tienes el rol admin/editor requerido
        500:
            description: Error al eliminar la vista de consulta
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Llamar al servicio para eliminar una vista de consulta
    return services.delete(view_id, current_user)

# Nuevo POST endpoint para crear una nueva vista de consulta
@bp.route('', methods=['POST'])
@jwt_required()
def new_view():
    """
    Crear una nueva vista de consulta
    ---
    security:
        - JWT: []
    tags:
        - Vistas
    consumes:
        - multipart/form-data
    description: >
        Requiere el rol "admin" o "editor". El cuerpo es `multipart/form-data`, no JSON: un
        campo `data` con el JSON serializado de la vista (ver el modelo View: name, slug,
        description, parent, root, visible son obligatorios; defaultView por defecto es
        "list") y, opcionalmente, UN único archivo de imagen bajo `files` como thumbnail.
    parameters:
        - in: formData
          name: data
          type: string
          required: true
          description: 'JSON serializado, p.ej. {"name": "...", "slug": "...", "description": "...", "parent": "", "root": "...", "visible": ["..."]}'
        - in: formData
          name: files
          type: file
          required: false
          description: A lo sumo un archivo de imagen (jpg/jpeg/png/gif/tif/tiff/heic/bmp/webp)
    responses:
        201:
            description: Vista de consulta creada exitosamente
        400:
            description: Se envió más de un archivo, o el archivo no es una imagen soportada
        401:
            description: No tienes el rol admin/editor requerido
        500:
            description: Error al crear la vista de consulta (p.ej. falta un campo requerido en "data", o falla el procesamiento de la imagen)
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401
    # Obtener el body del request
    body = request.form.to_dict()
    data = body.get('data')
    data = json.loads(data)
    
    files = request.files.getlist('files')
    # Llamar al servicio para crear la vista de consulta
    return services.create(data, current_user, files)
