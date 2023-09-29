from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required

class ExtendedPluginClass(PluginClass):
    def __init__(self, path, import_name, name, description, version, author):
        super().__init__(path, import_name, name, description, version, author)

    def add_route(self):
        @self.route('/<id>', methods=['GET'])
        @jwt_required()
        def index(id):
            return f'Hello, World! ID: {id}'
        
    
plugin_info = {
    'name': 'Procesamiento de archivos',
    'description': 'Plugin para procesar archivos y generar versiones para consulta en el gestor documental',
    'version': '0.1',
    'author': 'Néstor Andrés Peña'
}

plugin = ExtendedPluginClass('fileprocessing', __name__ , **plugin_info)