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
from app.api.system.services import set_value_in_dict, validate_text
from app.api.resources.models import ResourceUpdate
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


            if len(data['files']) == 0:
                return {'msg': 'No se subió un archivo'}, 400
            
            for f in files:
                try:
                    filename = secure_filename(f.filename)
                    if self.allowedFile(filename, ['xlsx']):
                        filename_new = self.save_temp_file(f, filename)
                        path = os.path.join(TEMPORAL_FILES_PATH, filename_new)
                        task = self.update.delay(path, data['overwrite'], current_user)
                        self.add_task_to_user(task.id, 'massiveUpdater.update_inventory', current_user, 'file_download')
                    
                    else:
                        return {'msg': 'Archivo no permitido'}, 400
                except Exception as e:
                    print(str(e))
                    return {'msg': 'Error al subir el archivo'}, 500
            

            return {'msg': 'Se cargó el archivo y se agregó la tarea a la fila de procesamientos. Puedes revisar en tu perfil cuando haya terminado y descargar el reporte.'}, 201
        
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
            
        
    @shared_task(ignore_result=False, name='massiveUpdater.update_inventory')
    def update(path, overwrite, user):
        reporte = []
        errores = []
        # abrir el archivo excel y leer la hoja Recursos
        df = pd.read_excel(path, sheet_name='Recursos')

        # quitamos el encabezado y dejamos la segunda fila como encabezado
        new_header = df.iloc[0]
        df = df[1:]
        df.columns = new_header

        for index, row in df.iterrows():
            # recuperamos el recurso
            resource = mongodb.get_record('resources', {'_id': ObjectId(row['id'])}, {'_id': 1, 'metadata': 1, 'post_type': 1})

            update = {}
            
            if resource == None:
                error = {
                    'index': index,
                    'id': row['id'],
                    'error': 'Recurso no encontrado'
                }
                errores.append(error)
                continue

            # recuperamos el tipo de contenido
            type = get_by_slug(resource['post_type'])

            fields = type['metadata']['fields']

            # iteramos sobre los campos del archivo
            for field in fields:
                try:
                    # si el campo no está en el archivo, lo dejamos como está
                    if field['destiny'] not in row:
                        continue
                    # else si row[field['destiny']] es Nan, lo dejamos como está
                    if pd.isna(row[field['destiny']]):
                        continue

                    if field['type'] == 'text' or field['type'] == 'text-area':
                        validate_text(row[field['destiny']], field)
                        set_value_in_dict(update, field['destiny'], row[field['destiny']])

                except Exception as e:
                    error = {
                        'index': index,
                        'id': row['id'],
                        'error': str(e)
                    }
                    errores.append(error)
                    continue

            
            # actualizamos el recurso
            resource = ResourceUpdate(**update)
            updated_resource = mongodb.update_record('resources', {'_id': ObjectId(row['id'])}, resource)
            reporte.append({
                'id': row['id'],
                'status': 'Actualizado'
            })

        # save the report
        folder_path = USER_FILES_PATH + '/' + user + '/massiveUpdater'
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
        file_id = str(uuid.uuid4())
        with pd.ExcelWriter(folder_path + '/' + file_id + '.xlsx') as writer:
            df = pd.DataFrame(errores)
            df.to_excel(writer, sheet_name='Errores', index=False)
            df = pd.DataFrame(reporte)
            df.to_excel(writer, sheet_name='Reporte', index=False)

        # borramos el archivo temporal
        os.remove(path)

        return '/' + user + '/massiveUpdater/' + file_id + '.xlsx'

        
    
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