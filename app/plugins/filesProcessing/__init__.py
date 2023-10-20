from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.utils import DatabaseHandler
from flask import request
from celery import shared_task
from dotenv import load_dotenv
import os
from .utils import AudioProcessing
from .utils import VideoProcessing

load_dotenv()

mongodb = DatabaseHandler.DatabaseHandler()
USER_FILES_PATH = os.environ.get('USER_FILES_PATH', '')
WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')
ORIGINAL_FILES_PATH = os.environ.get('ORIGINAL_FILES_PATH', '')

class ExtendedPluginClass(PluginClass):
    def __init__(self, path, import_name, name, description, version, author, type, settings):
        super().__init__(path, __file__, import_name, name, description, version, author, type, settings)

    def add_routes(self):
        @self.route('/bulk', methods=['POST'])
        @jwt_required()
        def process_files():
            current_user = get_jwt_identity()
            body = request.get_json()

            if 'post_type' not in body:
                return {'msg': 'No se especificó el tipo de contenido'}, 400

            task = self.bulk.delay(body, current_user)
            self.add_task_to_user(task.id, 'filesProcessing.create_webfile', current_user, 'msg')
            
            return {'msg': 'Se agregó la tarea a la fila de procesamientos'}, 201
        
    @shared_task(ignore_result=False, name='filesProcessing.create_webfile.auto')
    def auto_bulk(self, params):
        return 'ok'
        
    @shared_task(ignore_result=False, name='filesProcessing.create_webfile')
    def bulk(body, user):
        filters = {
            'post_type': body['post_type']
        }

        if 'parent' in body:
            if body['parent']:
                filters['parents.id'] = body['parent']

        # obtenemos los recursos
        resources = list(mongodb.get_all_records('resources', filters, fields={'_id': 1}))
        resources = [str(resource['_id']) for resource in resources]
        records = list(mongodb.get_all_records('records', {'parent.id': {'$in': resources}}, fields={'_id': 1, 'mime': 1, 'filepath': 1}))

        size = len(records)
        for file in records:
            path = os.path.join(ORIGINAL_FILES_PATH, file['filepath'])
            # quitar el nombre del archivo de la ruta
            path_dir = os.path.dirname(file['filepath'])
            # obtener el nombre del archivo sin la extensión
            filename = os.path.splitext(os.path.basename(file['filepath']))[0]
            
            if not os.path.exists(os.path.join(WEB_FILES_PATH, path_dir)):
                os.makedirs(os.path.join(WEB_FILES_PATH, path_dir))

            if 'audio' in file['mime']:
                AudioProcessing.main(path, os.path.join(WEB_FILES_PATH, path_dir, filename))
            elif 'video' in file['mime']:
                VideoProcessing.main(path, os.path.join(WEB_FILES_PATH, path_dir, filename))

        return 'Se procesaron ' + str(size) + ' archivos'
        
    
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