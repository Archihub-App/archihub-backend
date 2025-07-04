from app.api.system import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.system import services
from app.api.users import services as user_services
from flask import request
from app.utils.FernetAuth import fernetAuthenticate, nodeFernetAuthenticate
from flask_babel import _


# from app.tasks.tasks import add
from celery.result import AsyncResult

from flask import current_app as app

# En este archivo se registran las rutas de la API para los ajustes del sistema

# GET para obtener todos los ajustes del sistema
@bp.route('', methods=['GET'])
@jwt_required()
def get_all():
    """
    Obtener todos los ajustes del sistema
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Lista de ajustes del sistema
        401:
            description: No tiene permisos para obtener los ajustes del sistema
        500:
            description: Error al obtener los ajustes del sistema
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Llamar al servicio para obtener todos los ajustes del sistema
    resp = services.get_all_settings()
    return resp

# PUT para actualizar los ajustes del sistema
@bp.route('', methods=['PUT'])
@jwt_required()
def update():
    """
    Actualizar los ajustes del sistema
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Ajustes del sistema actualizados exitosamente
        401:
            description: No tiene permisos para actualizar los ajustes del sistema
        500:
            description: Error al actualizar los ajustes del sistema
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Obtener el body de la request
    body = request.get_json()

    # Llamar al servicio para actualizar los ajustes del sistema
    return services.update_settings(body, current_user)

# GET para obtener el tipo por defecto del modulo de catalogacion
@bp.route('/default-cataloging-type', methods=['GET'])
@jwt_required()
def get_default_cataloging_type():
    """
    Obtener el tipo por defecto del modulo de catalogacion
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Tipo por defecto del modulo de catalogacion
        404:
            description: No existe el tipo por defecto del modulo de catalogacion
        500:
            description: Error al obtener el tipo por defecto del modulo de catalogacion
    """
    # Llamar al servicio para obtener el tipo por defecto del modulo de catalogacion
    resp = services.get_default_cataloging_type()
    if isinstance(resp, list):
        return tuple(resp)
    return resp

# GET para obtener el listado de plugins en la carpeta plugins
@bp.route('/plugins', methods=['GET'])
@jwt_required()
def get_plugins():
    """
    Obtener el listado de plugins en la carpeta plugins
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Listado de plugins en la carpeta plugins
        401:
            description: No tiene permisos para obtener el listado de plugins en la carpeta plugins
        500:
            description: Error al obtener el listado de plugins en la carpeta plugins
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if user_services.has_role(current_user, 'processing') or user_services.has_role(current_user, 'admin'):
        # Llamar al servicio para obtener el listado de plugins en la carpeta plugins
        resp = services.get_plugins()
        if isinstance(resp, list):
            return tuple(resp)
        return resp	
    
    else:
        return {'msg': _('You don\'t have the required authorization')}, 401

# POST para instalar un plugin
@bp.route('/plugins', methods=['POST'])
@jwt_required()
def activate_plugin():
    """
    Activar plugins
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Plugins activados exitosamente
        401:
            description: No tiene permisos para activar los plugins
        500:
            description: Error al activar los plugins
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Obtener el body de la request
    body = request.get_json()
    # Llamar al servicio para instalar un plugin
    return services.activate_plugin(body, current_user)

# GET para cambiar activar/desactivar un plugin
@bp.route('/plugins/<plugin_name>', methods=['GET'])
@jwt_required()
def change_plugin_status(plugin_name):
    """
    Activar/desactivar un plugin
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    parameters:
        - in: path
          name: plugin_name
          schema:
            type: string
          required: true
          description: Nombre del plugin
    responses:
        200:
            description: Plugin activado/desactivado exitosamente
        401:
            description: No tiene permisos para activar/desactivar el plugin
        500:
            description: Error al activar/desactivar el plugin
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Llamar al servicio para activar/desactivar un plugin
    return services.change_plugin_status(plugin_name, current_user)

# GET para obtener el listado de access rights
@bp.route('/access-rights', methods=['GET'])
@jwt_required()
def get_access_rights():
    """
    Obtener el listado de access rights
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Listado de access rights
        401:
            description: No tiene permisos para obtener el listado de access rights
        500:
            description: Error al obtener el listado de access rights
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'editor'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Llamar al servicio para obtener el listado de access rights
    return services.get_access_rights()

# GET para obterner el listado de roles
@bp.route('/roles', methods=['GET'])
@jwt_required()
def get_roles():
    """
    Obtener el listado de roles
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Listado de roles
        401:
            description: No tiene permisos para obtener el listado de roles
        500:
            description: Error al obtener el listado de roles
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Llamar al servicio para obtener el listado de roles
    return services.get_roles()

# GET para iniciar la regeneración del index
@bp.route('/regenerate-index', methods=['GET'])
@jwt_required()
def regenerate_index():
    """
    Iniciar la regeneración del index
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Regeneración del index iniciada exitosamente
        401:
            description: No tiene permisos para iniciar la regeneración del index
        500:
            description: Error al iniciar la regeneración del index
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de procesamiento o administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Llamar al servicio para iniciar la regeneración del index
    return services.regenerate_index(current_user)

@bp.route('/index-resources', methods=['GET'])
@jwt_required()
def index_resources():
    """
    Iniciar la indexación de recursos
    ---
    security:
        - JWT: []
    tags:
       - Ajustes del sistema
    responses:
        200:
            description: Indexación de recursos iniciada exitosamente
        401:
            description: No tiene permisos para iniciar la indexación de recursos
        500:
            description: Error al iniciar la indexación de recursos
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de procesamiento o administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Llamar al servicio para iniciar la indexación de recursos
    return services.index_resources(current_user)

@bp.route('/index-geometries', methods=['GET'])
@jwt_required()
def index_geometries():
    """
    Iniciar la indexación de geometrías
    ---
    security:
        - JWT: []
    tags:
       - Ajustes del sistema
    responses:
        200:
            description: Indexación de recursos iniciada exitosamente
        401:
            description: No tiene permisos para iniciar la indexación de recursos
        500:
            description: Error al iniciar la indexación de recursos
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de procesamiento o administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Llamar al servicio para iniciar la indexación de geometrías
    return services.index_geometries(current_user)

@bp.route('/regenerate-index-geometries', methods=['GET'])
@jwt_required()
def regenerate_index_geometries():
    """
    Iniciar la regeneración del index de geometrías
    ---
    security:
        - JWT: []
    tags:
       - Ajustes del sistema
    responses:
        200:
            description: Indexación de recursos iniciada exitosamente
        401:
            description: No tiene permisos para iniciar la indexación de recursos
        500:
            description: Error al iniciar la indexación de recursos
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de procesamiento o administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Llamar al servicio para iniciar la indexación de geometrías
    return services.regenerate_index_geometries(current_user)

@bp.route('/clear-cache', methods=['GET'])
@jwt_required()
def clear_cache():
    """
    Limpiar la cache
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Cache limpiada exitosamente
        401:
            description: No tiene permisos para limpiar la cache
        500:
            description: Error al limpiar la cache
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de procesamiento o administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    # Llamar al servicio para limpiar la cache
    return services.clear_cache()

@bp.route('/node-clear-cache', methods=['GET'])
@nodeFernetAuthenticate
def node_clear_cache(user):
    """
    Limpiar la cache desde los nodos de procesamiento
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Cache limpiada exitosamente
        401:
            description: No tiene permisos para limpiar la cache
        500:
            description: Error al limpiar la cache
    """
    
    # Llamar al servicio para limpiar la cache
    return services.clear_cache()

@bp.route('/geo-load', methods=['GET'])
@jwt_required()
def geo_load():
    """
    Cargar poligonos de georeferenciación
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Polígonos geográficos actualizados
        401:
            description: No tiene permisos para actualizar los poligonos
        500:
            description: Error al actualizar los poligonos
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de procesamiento o administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    
    
    from app.api.geosystem.services import upload_shapes
    return upload_shapes()

@bp.route('/zip-files-delete', methods=['GET'])
@jwt_required()
def zip_files_delete():
    """
    Eliminar archivos zip
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Archivos eliminados exitosamente
        401:
            description: No tiene permisos para eliminar los archivos
        500:
            description: Error al eliminar los archivos
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de procesamiento o administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    
    from app.api.resources.services import delete_zip_files
    return delete_zip_files()

@bp.route('/inventory_files_delete', methods=['GET'])
@jwt_required()
def inventory_files_delete():
    """
    Eliminar archivos excel de inventario
    ---
    security:
        - JWT: []
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Archivos eliminados exitosamente
        401:
            description: No tiene permisos para eliminar los archivos
        500:
            description: Error al eliminar los archivos
    """
    # Obtener el usuario actual
    current_user = get_jwt_identity()
    # Verificar si el usuario tiene el rol de procesamiento o administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    
    from app.api.resources.services import delete_inventory_files
    return delete_inventory_files()

@bp.route('/get-settings', methods=['GET'])
def get_system_settings():
    """
    Obtener el idioma del sistema
    ---
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Idioma del sistema
        500:
            description: Error al obtener el idioma del sistema
    """
    resp = services.get_system_settings()
    if isinstance(resp, list):
        return tuple(resp)
    return resp

@bp.route('/get-actions', methods=['POST'])
@jwt_required()
def get_actions():
    """
    Obtener las acciones del sistema
    ---
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Acciones del sistema
        500:
            description: Error al obtener las acciones del sistema
    """
    body = request.get_json()
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing') and not user_services.has_role(current_user, 'editor'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    
    resp = services.get_system_actions(body['placement'])
    if isinstance(resp, list):
        return tuple(resp)
    return resp

@bp.route('/restart', methods=['GET'])
@jwt_required()
def restart():
    """
    Reiniciar el sistema
    ---
    tags:
        - Ajustes del sistema
    responses:
        200:
            description: Sistema reiniciado exitosamente
        500:
            description: Error al reiniciar el sistema
    """

    # Obtener el usuario actual
    current_user = get_jwt_identity()

    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    
    # Llamar al servicio para reiniciar el sistema
    return services.restart_system()