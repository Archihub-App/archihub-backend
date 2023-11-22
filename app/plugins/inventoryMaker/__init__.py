from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required, get_jwt_identity
from celery import shared_task
from flask import request, send_file
from app.utils import DatabaseHandler
from app.api.types.services import get_by_slug
from app.api.resources.services import get_value_by_path
import os
import uuid
import pandas as pd
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
        
        @self.route('/filedownload/<taskId>', methods=['GET'])
        @jwt_required()
        def file_download(taskId):
            current_user = get_jwt_identity()

            if not self.has_role('admin', current_user) and not self.has_role('processing', current_user):
                return {'msg': 'No tiene permisos suficientes'}, 401
            
            # Buscar la tarea en la base de datos
            task = mongodb.get_record('tasks', {'taskId': taskId})
            # Si la tarea no existe, retornar error
            if not task:
                return {'msg': 'Tarea no existe'}, 404
            
            if task['user'] != current_user and not self.has_role('admin', current_user):
                return {'msg': 'No tiene permisos para obtener la tarea'}, 401

            if task['status'] == 'pending':
                return {'msg': 'Tarea en proceso'}, 400

            if task['status'] == 'failed':
                return {'msg': 'Tarea fallida'}, 400

            if task['status'] == 'completed':
                if task['resultType'] != 'file_download':
                    return {'msg': 'Tarea no es de tipo file_download'}, 400
                
            path = USER_FILES_PATH + task['result']
            return send_file(path, as_attachment=True)

            
        
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

        resources = [str(resource['_id']) for resource in resources]

        records_filters = {
            'parent.id': {'$in': resources},
        }

        records = list(mongodb.get_all_records('records', records_filters, fields={
            '_id': 1, 'mime': 1, 'filepath': 1, 'hash': 1, 'size': 1}))
        
        for r in records:
            obj = {
                'id': str(r['_id']),
                'mime': r['mime'],
                'filepath': r['filepath'],
                'hash': r['hash'],
                'size': r['size']
            }

            records_df.append(obj)

        folder_path = USER_FILES_PATH + '/' + user + '/inventoryMaker'
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

        file_id = str(uuid.uuid4())

        with pd.ExcelWriter(folder_path + '/' + file_id + '.xlsx') as writer:
            df = pd.DataFrame(resources_df)
            df.to_excel(writer, sheet_name='Recursos', index=False)
            df = pd.DataFrame(records_df)
            df.to_excel(writer, sheet_name='Archivos', index=False)

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