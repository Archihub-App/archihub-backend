from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required
from celery import shared_task

class ExtendedPluginClass(PluginClass):
    def __init__(self, path, import_name, name, description, version, author, type):
        super().__init__(path, __file__, import_name, name, description, version, author, type)

    def add_routes(self):
        @self.route('/<id>', methods=['GET'])
        # @jwt_required()
        def index(id):
            task = self.add.delay(1, 2)
            print(task.id)
            return f'Hello, World! ID: {id}'
        
    @shared_task(ignore_result=False, name='filesProcessing.create_webfile')
    def auto_bulk(self, params):
        return 'ok'
        
    @shared_task(ignore_result=False)
    def add(x, y):
        print(x + y)
        return x + y
        
    
plugin_info = {
    'name': 'Procesamiento de archivos',
    'description': 'Plugin para procesar archivos y generar versiones para consulta en el gestor documental',
    'version': '0.1',
    'author': 'Néstor Andrés Peña',
    'type': ['bulk']
}