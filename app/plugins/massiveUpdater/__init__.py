from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required, get_jwt_identity
from celery import shared_task
from flask import request, send_file
from app.utils import DatabaseHandler
from app.api.types.services import get_by_slug
from app.api.resources.services import get_value_by_path
import os
from werkzeug.utils import secure_filename
import uuid
import pandas as pd
import json
from dotenv import load_dotenv
from bson.objectid import ObjectId
load_dotenv()

mongodb = DatabaseHandler.DatabaseHandler()
USER_FILES_PATH = os.environ.get('USER_FILES_PATH', '')
TEMPORAL_FILES_PATH = os.environ.get('TEMPORAL_FILES_PATH', '')

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
            
            body = request.form.to_dict()
            data = body['data']
            data = json.loads(data)

            files = request.files.getlist('files')

            reporte = []

            if len(data['files']) == 0:
                return {'msg': 'No se subió un archivo'}, 400
            
            for f in files:
                try:
                    filename = secure_filename(f.filename)
                    if self.allowedFile(filename, ['xlsx']):
                        filename_new = self.save_temp_file(f, filename)
                        path = os.path.join(TEMPORAL_FILES_PATH, filename_new)

                        # abrir el archivo excel y leer la hoja Recursos
                        df = pd.read_excel(path, sheet_name='Recursos')

                        # quitamos el encabezado y dejamos la segunda fila como encabezado
                        new_header = df.iloc[0]
                        df = df[1:]
                        df.columns = new_header

                        # iteramos sobre las filas del archivo
                        for index, row in df.iterrows():
                            # recuperamos el recurso
                            resource = mongodb.get_record('resources', {'_id': ObjectId(row['id'])}, {'_id': 1, 'metadata': 1, 'post_type': 1})
                            
                            if resource == None:
                                error = {
                                    'index': index,
                                    'id': row['id'],
                                    'error': 'Recurso no encontrado'
                                }
                                reporte.append(error)
                                continue

                            

                    
                    else:
                        return {'msg': 'Archivo no permitido'}, 400
                except:
                    return {'msg': 'Error al subir el archivo'}, 500
            
            task = self.update.delay(body, current_user)
            self.add_task_to_user(task.id, 'massiveUpdater.update_inventory', current_user, 'file_download')

            return {'msg': 'Se agregó la tarea a la fila de procesamientos'}, 201
            
        
    @shared_task(ignore_result=False, name='massiveUpdater.update_inventory')
    def update(body, user):
        return 'ok'

        
    
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
                'text': 'La actualización masiva de recursos permite actualizar los recursos del gestor documental de manera masiva. Para ello, se debe subir un archivo Excel con los recursos a actualizar. El archivo debe tener la misma estructura que el archivo de exportación de recursos.'
            },
            {
                'type': 'file',
                'name': 'file',
                'label': 'Archivo Excel',
                'required': True,
                'limit': 1,
                'acceptedFiles': ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
            },
            {
                'type': 'checkbox',
                'name': 'overwrite',
                'label': 'Espacio en blanco como borrado de contenido',
                'instructions': 'Si se selecciona esta opción, los campos en blanco en el archivo Excel se interpretarán como borrado de contenido.',
                'default': False
            }
        ]
    }
}