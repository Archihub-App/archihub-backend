from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required, get_jwt_identity
from celery import shared_task
from flask import request
from app.utils import DatabaseHandler
from app.api.types.services import get_by_slug
from app.api.resources.services import get_value_by_path
import os
import uuid
# leer variables de entorno desde el archivo .env
from dotenv import load_dotenv
import pandas as pd
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

        type = get_by_slug(body['post_type'])
        type_metadata = type['metadata']
        type_metadata_name = type['name']

        resources_df = []
        records_df = []

        if 'parent' in body:
            if body['parent']:
                filters['parents.id'] = body['parent']

        # buscamos los recursos con los filtros especificados
        resources = list(mongodb.get_all_records('resources', filters))

        # si no hay recursos, retornamos un error
        if len(resources) == 0:
            raise Exception('No se encontraron recursos con los filtros especificados')
        
        # si hay recursos, iteramos
        for r in resources:
            obj = {}
            
            for f in type_metadata['fields']:
                if f['type'] == 'text' or f['type'] == 'text-area':
                    obj[f['label']] = get_value_by_path(r, f['destiny'])

            resources_df.append(obj)

        folder_path = USER_FILES_PATH + '/' + user + '/inventoryMaker'
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

        file_id = str(uuid.uuid4())

        with pd.ExcelWriter(folder_path + '/' + file_id + '.xlsx') as writer:
            df = pd.DataFrame(resources_df)
            df.to_excel(writer, sheet_name='Recursos', index=False)

        return '/' + user + '/inventoryMaker/' + file_id + '.xlsx'
        
    
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