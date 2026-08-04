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
    Get all system settings
    ---
    security:
        - JWT: []
    tags:
        - System settings
    responses:
        200:
            description: List of system settings
        401:
            description: You don't have permission to retrieve the system settings
        500:
            description: Error retrieving the system settings
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
    Update the system settings
    ---
    security:
        - JWT: []
    tags:
        - System settings
    parameters:
        - in: body
          name: body
          required: true
          description: >-
              Object with any combination of the following top-level keys
              (all optional, only the ones present are updated); each value
              is in turn the complete `data` object of that setting
              (list of {id, value}); updates post_types_settings,
              access_rights, api_activation, index_management, user_management
              and/or files_management in the `system` collection.
          schema:
            type: object
            properties:
                post_types_settings:
                    type: object
                access_rights:
                    type: object
                api_activation:
                    type: object
                index_management:
                    type: object
                user_management:
                    type: object
                files_management:
                    type: object
    responses:
        200:
            description: System settings updated successfully
        401:
            description: You don't have permission to update the system settings
        500:
            description: Error updating the system settings
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
    Get the default type for the cataloging module
    ---
    security:
        - JWT: []
    tags:
        - System settings
    responses:
        200:
            description: Default type for the cataloging module
        404:
            description: The default type for the cataloging module does not exist
        500:
            description: Error retrieving the default type for the cataloging module
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
    Get the list of plugins in the plugins folder
    ---
    security:
        - JWT: []
    tags:
        - System settings
    responses:
        200:
            description: List of plugins in the plugins folder
        401:
            description: You don't have permission to retrieve the list of plugins in the plugins folder
        500:
            description: Error retrieving the list of plugins in the plugins folder
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
    Replace the list of active plugins
    ---
    security:
        - JWT: []
    tags:
        - System settings
    parameters:
        - in: body
          name: body
          required: true
          description: >-
              Array (not an object) with the names of the plugin folders
              (under app/plugins/) to activate; any name that does not
              correspond to a valid plugin folder (with __init__.py)
              is silently ignored. Completely replaces the `active_plugins`
              record, requests a hot restart (SIGHUP) of the process.
          schema:
            type: array
            items:
                type: string
    responses:
        200:
            description: Plugins activated successfully, a restart was requested
        401:
            description: You don't have permission to activate the plugins
        500:
            description: Error activating the plugins
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
    Activate/deactivate a plugin
    ---
    security:
        - JWT: []
    tags:
        - System settings
    parameters:
        - in: path
          name: plugin_name
          schema:
            type: string
          required: true
          description: Plugin name
    responses:
        200:
            description: Plugin activated/deactivated successfully
        401:
            description: You don't have permission to activate/deactivate the plugin
        500:
            description: Error activating/deactivating the plugin
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
    Get the list of access rights
    ---
    security:
        - JWT: []
    tags:
        - System settings
    responses:
        200:
            description: List of access rights
        401:
            description: You don't have permission to retrieve the list of access rights
        500:
            description: Error retrieving the list of access rights
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
    Get the list of roles
    ---
    security:
        - JWT: []
    tags:
        - System settings
    responses:
        200:
            description: List of roles
        401:
            description: You don't have permission to retrieve the list of roles
        500:
            description: Error retrieving the list of roles
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
    Start regenerating the index
    ---
    security:
        - JWT: []
    tags:
        - System settings
    responses:
        200:
            description: Index regeneration started successfully (queued in Celery)
        400:
            description: Indexing is disabled in index_management
        401:
            description: You don't have permission to start the index regeneration
        404:
            description: The index_management record does not exist in the system collection
        500:
            description: Error starting the index regeneration
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
    Start the full indexing of resources
    ---
    security:
        - JWT: []
    tags:
       - System settings
    responses:
        200:
            description: Resource indexing started successfully (queued in Celery)
        400:
            description: Indexing is not enabled in index_management
        401:
            description: You don't have permission to start the resource indexing
        404:
            description: The index_management record does not exist in the system collection
        500:
            description: Error starting the resource indexing
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
    Start indexing geometries
    ---
    security:
        - JWT: []
    tags:
       - System settings
    responses:
        200:
            description: Geometry indexing started successfully (queued in Celery)
        401:
            description: You don't have permission to start the geometry indexing
        500:
            description: Error starting the geometry indexing
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
    Start regenerating the geometries index
    ---
    security:
        - JWT: []
    tags:
       - System settings
    responses:
        200:
            description: Geometries index regeneration started successfully (queued in Celery)
        401:
            description: You don't have permission to start the geometries index regeneration
        500:
            description: Error starting the geometries index regeneration
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
    Clear the cache
    ---
    security:
        - JWT: []
    tags:
        - System settings
    responses:
        200:
            description: Cache cleared successfully
        401:
            description: You don't have permission to clear the cache
        500:
            description: Error clearing the cache
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
    Clear the cache from the processing nodes (node authentication)
    ---
    security:
        - JWT: []
    tags:
        - System settings
    description: >-
        Does NOT use a normal login JWT: requires `Authorization: Bearer
        <token>` where `<token>` is a JWT signed with JWT_SECRET_KEY and
        then encrypted with FERNET_KEY (see app/utils/FernetAuth.py,
        nodeFernetAuthenticate) — meant for node-to-node calls
        (NODE_TOKEN), not for frontend user sessions.
    responses:
        200:
            description: Cache cleared successfully
        401:
            description: Missing, invalid, or expired node token, or nonexistent user
        500:
            description: Error clearing the cache
    """
    
    # Llamar al servicio para limpiar la cache
    return services.clear_cache()

@bp.route('/geo-load', methods=['GET'])
@jwt_required()
def geo_load():
    """
    Load georeferencing polygons
    ---
    security:
        - JWT: []
    tags:
        - System settings
    responses:
        200:
            description: Geographic polygons updated
        401:
            description: You don't have permission to update the polygons
        500:
            description: Error updating the polygons
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
    Delete zip files
    ---
    security:
        - JWT: []
    tags:
        - System settings
    responses:
        200:
            description: Files deleted successfully
        401:
            description: You don't have permission to delete the files
        500:
            description: Error deleting the files
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
    Delete inventory excel files
    ---
    security:
        - JWT: []
    tags:
        - System settings
    responses:
        200:
            description: Files deleted successfully
        401:
            description: You don't have permission to delete the files
        500:
            description: Error deleting the files
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
    Get the system language
    ---
    tags:
        - System settings
    responses:
        200:
            description: System language
        500:
            description: Error retrieving the system language
    """
    resp = services.get_system_settings()
    if isinstance(resp, list):
        return tuple(resp)
    return resp

@bp.route('/set-first-time', methods=['POST'])
def set_first_time():
    """
    Set the system's first-time initialization (creates the initial admin user)
    ---
    tags:
        - System settings
    description: >-
        Only works while the `first_time` setting is still active (it is
        deactivated after the first successful run); creates the initial
        admin user with the admin/editor/user/super_editor/publisher roles
        and, if the post_types/forms/users collections don't already exist,
        creates the default content type and form according to `typeTemplate`.
    parameters:
        - in: body
          name: body
          required: true
          schema:
            type: object
            required:
                - username
                - password
                - confirmPassword
                - typeTemplate
            properties:
                username:
                    type: string
                    description: Also used as the admin user's email
                password:
                    type: string
                confirmPassword:
                    type: string
                typeTemplate:
                    type: string
                    description: 'Initial content type template, e.g.: "basic"'
    responses:
        200:
            description: First-time initialization set successfully
        400:
            description: >-
                The system was already configured, required fields are
                missing, a field was empty, or the user already exists
        500:
            description: Error setting the first-time initialization
    """
    body = request.get_json()
    return services.set_first_time(body)

@bp.route('/get-actions', methods=['POST'])
@jwt_required()
def get_actions():
    """
    Get the system actions for a UI placement
    ---
    security:
        - JWT: []
    tags:
        - System settings
    parameters:
        - in: body
          name: body
          required: true
          schema:
            type: object
            required:
                - placement
            properties:
                placement:
                    type: string
                    description: UI location for which the available actions are requested
    responses:
        200:
            description: System actions
        401:
            description: You don't have permission to retrieve the system actions
        500:
            description: Error retrieving the system actions
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
    Restart the system
    ---
    security:
        - JWT: []
    tags:
        - System settings
    responses:
        200:
            description: System restart requested successfully
        401:
            description: You don't have permission to restart the system
        500:
            description: Error restarting the system
    """

    # Obtener el usuario actual
    current_user = get_jwt_identity()

    # Verificar si el usuario tiene el rol de administrador
    if not user_services.has_role(current_user, 'admin'):
        return {'msg': _('You don\'t have the required authorization')}, 401
    
    # Llamar al servicio para reiniciar el sistema
    return services.restart_system()