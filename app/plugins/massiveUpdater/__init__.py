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
        @self.route('/lunch', methods=['POST'])
        @jwt_required()
        def update_inventory():
            # get the current user
            current_user = get_jwt_identity()

            if not self.has_role('admin', current_user) and not self.has_role('processing', current_user):
                return {'msg': 'No tiene permisos suficientes'}, 401
            
            body = request.get_json()

            if 'post_type' not in body:
                return {'msg': 'No se especificó el tipo de contenido'}, 400
            
            task = self.update.delay(body, current_user)
            self.add_task_to_user(task.id, 'massiveUpdater.update_inventory', current_user, 'file_download')

            return {'msg': 'Se agregó la tarea a la fila de procesamientos'}, 201
            
        
    @shared_task(ignore_result=False, name='massiveUpdater.update_inventory')
    def update(body, user):
        pass

        
    
plugin_info = {
    'name': 'Actualización masiva de recursos',
    'description': 'Plugin para actualizar masivamente los recursos del gestor documental.',
    'version': '0.1',
    'author': 'Néstor Andrés Peña',
    'type': ['lunch'],
    'settings': {
        'settings_lunch': [
            {
                'type':  'instructions',
                'title': 'Instrucciones',
                'text': 'La actualización masiva de recursos permite actualizar los recursos del gestor documental de manera masiva. Para ello, se debe subir un archivo CSV con los recursos a actualizar. El archivo debe tener la misma estructura que el archivo de exportación de recursos.'
            },
            {
                'type': 'file',
                'name': 'file',
                'label': 'Archivo CSV',
                'required': True
            },
            {
                'type': 'checkbox',
                'name': 'overwrite',
                'label': 'Sobreescribir',
                'required': True,
                'text': 'Sobreescribir los recursos existentes'
            }
        ]
    }
}