from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required
from celery import shared_task

class ExtendedPluginClass(PluginClass):
    def __init__(self, path, import_name, name, description, version, author):
        super().__init__(path, import_name, name, description, version, author)

    def add_route(self):
        @self.route('/<id>', methods=['GET'])
        # @jwt_required()
        def index(id):
            task = self.add.delay(1, 2)
            print(task.id)
            return f'Hello, World! ID: {id}'
        
    @shared_task(ignore_result=False)
    def add(x, y):
        print(x + y)
        return x + y
        
    
plugin_info = {
    'name': 'Procesamiento de archivos',
    'description': 'Plugin para procesar archivos y generar versiones para consulta en el gestor documental',
    'version': '0.1',
    'author': 'Néstor Andrés Peña'
}