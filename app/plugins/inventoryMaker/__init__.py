from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required, get_jwt_identity
from celery import shared_task
from flask import request
from app.utils import DatabaseHandler
import os
# leer variables de entorno desde el archivo .env
from dotenv import load_dotenv
load_dotenv()

mongodb = DatabaseHandler.DatabaseHandler()
USER_FILES_PATH = os.environ.get('USER_FILES_PATH', '')

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
            
            body = request.get_json()

            if 'post_type' not in body:
                return {'msg': 'No se especificó el tipo de contenido'}, 400
            
            task = self.create.delay(body, current_user)

            self.add_task_to_user(task.id, 'inventoryMaker.create_inventory', current_user, 'file_download')

            return {'msg': 'Se agregó la tarea a la fila de procesamientos'}, 201
            
        
    @shared_task(ignore_result=False, name='inventoryMaker.create_inventory')
    def create(body, user):
        filters = {
            'post_type': body['post_type']
        }

        if 'parent' in body:
            filters['parents.id'] = body['parent']
        # buscamos los recursos con los filtros especificados
        resources = mongodb.get_all_records('resources', filters)

        return 'ok'
        
    
plugin_info = {
    'name': 'Exportar inventarios',
    'description': 'Plugin para exportar inventarios del gestor documental',
    'version': '0.1',
    'author': 'Néstor Andrés Peña',
    'type': ['bulk'],
    'settings': {
        'settings_bulk': [
            {
                'type':  'instructions',
                'title': 'Instrucciones',
                'text': 'Este plugin permite generar inventarios en archivo excel del contenido del gestor documental. Para ello, puede especificar el tipo de contenido sobre el cual quiere generar el inventario y los filtros que desea aplicar. El archivo se encontrará en su perfil para su descarga una vez se haya terminado de generar. Es importante notar que el proceso de generación de inventarios puede tardar varios minutos, dependiendo de la cantidad de recursos que se encuentren en el gestor documental.',
            }
        ]
    }
}