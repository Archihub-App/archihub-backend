from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required, get_jwt_identity
from celery import shared_task
from flask import request
from app.utils import DatabaseHandler

mongodb = DatabaseHandler.DatabaseHandler()


class ExtendedPluginClass(PluginClass):
    def __init__(self, path, import_name, name, description, version, author, type):
        super().__init__(path, import_name, name, description, version, author, type)

    def add_route(self):
        @self.route('', methods=['POST'])
        @jwt_required()
        def create_inventory():
            # get the current user
            current_user = get_jwt_identity()
            # get the request body
            body = request.get_json()

            # get the mongodb instance
            task = self.add.delay(4,2)

            self.add_task_to_user(task.id, 'current_user')

            return 'ok'
            
        
    @shared_task(ignore_result=False, name='inventoryMaker.create_inventory')
    def add( x, y):
        # buscamos los recursos con los filtros especificados
        resources = mongodb.get_all_records('resources', {})

        return x + y
        
    
plugin_info = {
    'name': 'Exportar inventarios',
    'description': 'Plugin para exportar inventarios del gestor documental',
    'version': '0.1',
    'author': 'Néstor Andrés Peña',
    'type': ['bulk']
}