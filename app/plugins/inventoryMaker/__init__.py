from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required, get_jwt_identity
from celery import shared_task

class ExtendedPluginClass(PluginClass):
    def __init__(self, path, import_name, name, description, version, author):
        super().__init__(path, import_name, name, description, version, author)

    def add_route(self):
        @self.route('', methods=['POST'])
        @jwt_required()
        def create_inventory():
            # get the current user
            current_user = get_jwt_identity()
            

        
    @shared_task(ignore_result=False)
    def add(x, y):
        print(x + y)
        return x + y
        
    
plugin_info = {
    'name': 'Exportar inventarios',
    'description': 'Plugin para exportar inventarios del gestor documental',
    'version': '0.1',
    'author': 'Néstor Andrés Peña'
}