from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required, get_jwt_identity
from celery import shared_task
from flask import request
from app.utils import DatabaseHandler

mongodb = DatabaseHandler.DatabaseHandler()


class ExtendedPluginClass(PluginClass):
    def __init__(self, path, import_name, name, description, version, author, type, settings):
        super().__init__(path, __file__, import_name, name, description, version, author, type, settings)

    def add_routes(self):
        @self.route('/bulk', methods=['POST'])
        @jwt_required()
        def create_inventory():
            # get the current user
            current_user = get_jwt_identity()

            if not self.has_role('admin', current_user) and not self.has_role('processing', current_user):
                return {'msg': 'No tiene permisos suficientes'}, 401
            # get the request body
            body = request.get_json()

            # get the mongodb instance
            task = self.add.delay(body, current_user)

            self.add_task_to_user(task.id, 'inventoryMaker.create_inventory', current_user, 'file_download')

            return 'ok'
            
        
    @shared_task(ignore_result=False, name='inventoryMaker.create_inventory')
    def add(body, user):
        # buscamos los recursos con los filtros especificados
        resources = mongodb.get_all_records('resources', {})

        return 'ok'
        
    
plugin_info = {
    'name': 'Exportar inventarios',
    'description': 'Plugin para exportar inventarios del gestor documental',
    'version': '0.1',
    'author': 'Néstor Andrés Peña',
    'type': ['bulk'],
    'settings': [
        {
            'name': 'bulk_settings',
            'fields': [

            ]
        }
    ]
}