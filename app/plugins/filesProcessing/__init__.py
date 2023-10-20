from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required
from app.utils import DatabaseHandler
from celery import shared_task
from dotenv import load_dotenv
import os
load_dotenv()

mongodb = DatabaseHandler.DatabaseHandler()
USER_FILES_PATH = os.environ.get('USER_FILES_PATH', '')
WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')

class ExtendedPluginClass(PluginClass):
    def __init__(self, path, import_name, name, description, version, author, type, settings):
        super().__init__(path, __file__, import_name, name, description, version, author, type, settings)

    def add_routes(self):
        @self.route('/bulk', methods=['POST'])
        @jwt_required()
        def process_files():
            
            return {'msg': 'Se agregó la tarea a la fila de procesamientos'}, 201
        
    @shared_task(ignore_result=False, name='filesProcessing.create_webfile.auto')
    def auto_bulk(self, params):
        return 'ok'
        
    @shared_task(ignore_result=False, name='filesProcessing.create_webfile')
    def bulk(body, user):
        filters = {
            'post_type': body['post_type']
        }

        # buscamos los recursos con los filtros especificados
        resources = list(mongodb.get_all_records('resources', filters))
        
        return x + y
        
    
plugin_info = {
    'name': 'Procesamiento de archivos',
    'description': 'Plugin para procesar archivos y generar versiones para consulta en el gestor documental',
    'version': '0.1',
    'author': 'Néstor Andrés Peña',
    'type': ['bulk', 'settings'],
    'settings': {
        'settings': [

        ],
        'settings_bulk': [
            {
                'type':  'instructions',
                'title': 'Instrucciones',
                'text': 'Este plugin permite procesar archivos y generar versiones para consulta en el gestor documental. Para ello, puede especificar el tipo de contenido sobre el cual quiere generar las versiones y los filtros que desea aplicar. Es importante notar que el proceso de generación de versiones puede tardar varios minutos, dependiendo de la cantidad de recursos que se encuentren en el gestor documental y el tamaño original de los archivos.',
            }
        ]
    }
}