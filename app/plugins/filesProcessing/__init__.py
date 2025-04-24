from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.utils import DatabaseHandler
from app.utils import HookHandler
from flask import request
from celery import shared_task, current_task
from dotenv import load_dotenv
import os
from .utils import AudioProcessing
from .utils import VideoProcessing
from .utils import ImageProcessing
from .utils import PDFprocessing
from .utils import DocumentProcessing
from .utils import DatabaseProcessing
from bson.objectid import ObjectId
from app.api.types.services import get_all as get_all_types
import json
from flask_babel import _
import datetime

load_dotenv()

mongodb = DatabaseHandler.DatabaseHandler()
hookHandler = HookHandler.HookHandler()

USER_FILES_PATH = os.environ.get('USER_FILES_PATH', '')
WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')
ORIGINAL_FILES_PATH = os.environ.get('ORIGINAL_FILES_PATH', '')

def get_filename_extension(filename):
    if '.' not in filename:
        return None
    if len(filename.split('.')) != 2:
        return None
    ext = os.path.splitext(filename)[1]
    ext = ext.lower()
    return ext

def process_file(file, instance=None):
    path = os.path.join(ORIGINAL_FILES_PATH, file['filepath'])
    # quitar el nombre del archivo de la ruta
    path_dir = os.path.dirname(file['filepath'])
    # obtener el nombre del archivo sin la extensión
    filename = os.path.splitext(os.path.basename(file['filepath']))[0]
    
    if not os.path.exists(os.path.join(WEB_FILES_PATH, path_dir)):
        os.makedirs(os.path.join(WEB_FILES_PATH, path_dir))

    if 'audio' in file['mime']:
        result = AudioProcessing.main(path, os.path.join(WEB_FILES_PATH, path_dir, filename))
        if result:
            update = {
                'processing': {
                    'fileProcessing': {
                        'type': 'audio',
                        'path': os.path.join(path_dir, filename),
                    }
                }
            }
            instance.update_data('records', str(file['_id']), update)

    elif 'video' in file['mime']:
        result_audio, result_video = VideoProcessing.main(path, os.path.join(WEB_FILES_PATH, path_dir, filename))
        if result_video or result_audio:
            type = 'video' if result_video else 'audio' if result_audio else None
            update = {
                'processing': {
                    'fileProcessing': {
                        'type': type,
                        'path': os.path.join(path_dir, filename),
                    }
                }
            }
            
            instance.update_data('records', str(file['_id']), update)
    elif 'image' in file['mime']:
        result = ImageProcessing.main(path, os.path.join(WEB_FILES_PATH, path_dir, filename))
        if result:
            update = {
                'processing': {
                    'fileProcessing': {
                        'type': 'image',
                        'path': os.path.join(path_dir, filename),
                    }
                }
            }
            
            instance.update_data('records', str(file['_id']), update)
    elif 'word' in file['mime'] or ('text' in file['mime'] and get_filename_extension(file['filepath']) != '.csv'):
        result = DocumentProcessing.main(path, os.path.join(ORIGINAL_FILES_PATH, path_dir, filename), os.path.join(WEB_FILES_PATH, path_dir, filename))

        if result:
            update = {
                'processing': {
                    'fileProcessing': {
                        'type': 'document',
                        'path': os.path.join(path_dir, filename),
                    }
                }
            }
            
            instance.update_data('records', str(file['_id']), update)
    elif 'application/pdf' in file['mime']:
        result = PDFprocessing.main(path, os.path.join(WEB_FILES_PATH, path_dir, filename))
        folder_path = os.path.join(path_dir, filename).split('.')[0]

        if result:
            update = {
                'processing': {
                    'fileProcessing': {
                        'type': 'document',
                        'path': folder_path,
                    }
                }
            }
            
            instance.update_data('records', str(file['_id']), update)
    
    elif 'text' in file['mime'] and get_filename_extension(file['filepath']) == '.csv':
        result = DatabaseProcessing.main_csv(path, os.path.join(WEB_FILES_PATH, path_dir, filename))

        if result:
            update = {
                'processing': {
                    'fileProcessing': {
                        'type': 'database',
                        'path': os.path.join(path_dir, filename),
                    }
                }
            }
            
            instance.update_data('records', str(file['_id']), update)

    elif 'sheet' in file['mime']:
        result = DatabaseProcessing.main_excel(path, os.path.join(WEB_FILES_PATH, path_dir, filename))

        if result:
            update = {
                'processing': {
                    'fileProcessing': {
                        'type': 'database',
                        'path': os.path.join(path_dir, filename),
                    }
                }
            }
            
            instance.update_data('records', str(file['_id']), update)

class ExtendedPluginClass(PluginClass):
    def __init__(self, path, import_name, name, description, version, author, type, settings, actions, capabilities=None):
        super().__init__(path, __file__, import_name, name, description, version, author, type, settings, actions = actions, capabilities=None)
        if not os.environ.get('CELERY_WORKER'):
            self.activate_settings()

    @shared_task(ignore_result=False, name='filesProcessingCreate.auto')
    def automatic(type, body):
        instance = ExtendedPluginClass('filesProcessing','', **plugin_info)
        if body['post_type'] != type['type']:
            return 'ok'
        
        records_filters = {
            'parent.id': {'$in': [str(body['_id'])]},
            'processing.fileProcessing': {'$exists': False}
        }
        
        records = list(mongodb.get_all_records('records', records_filters, fields={'_id': 1, 'mime': 1, 'filepath': 1}))
        size = len(records)
        for file in records:
            process_file(file, instance)

        instance.clear_cache()
        return 'Se procesaron ' + str(size) + ' archivos'

    def activate_settings(self):
        current = self.get_plugin_settings()
        if 'types_activation' not in current:
            return
        
        types = current['types_activation']
        for t in types:
            hookHandler.register('resource_files_create', self.automatic, t, t['order'])

    def add_routes(self):
        @self.route('/bulk', methods=['POST'])
        @jwt_required()
        def process_files():
            current_user = get_jwt_identity()
            body = request.get_json()
            self.validate_fields(body, 'bulk')
            self.validate_roles(current_user, ['admin', 'processing'])
            task = self.bulk.delay(body, current_user)
            self.add_task_to_user(task.id, 'filesProcessing.create_webfile', current_user, 'msg', {
                'args': body
            })
            
            return {'msg': 'Se agregó la tarea a la fila de procesamientos'}, 201

    @shared_task(ignore_result=False, name='filesProcessing.create_webfile')
    def bulk(body, user):
        current_task.update_state(state='PROGRESS', meta={
            'status': _('Starting process'),
            'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        })
        filters = {
            'post_type': body['post_type']
        }
        if isinstance(body['post_type'], list):
            filters['post_type'] = {'$in': body['post_type']}   

        if 'parent' in body:
            if body['parent'] and len(body['resources']) == 0:
                filters = {'$or': [{'parents.id': body['parent'], 'post_type': filters['post_type']}, {'_id': ObjectId(body['parent'])}]}
                
        
        if 'resources' in body:
            if body['resources']:
                if len(body['resources']) > 0:
                    filters = {'_id': {'$in': [ObjectId(resource) for resource in body['resources']]}, **filters}

        total = mongodb.count('resources', filters)
        step = 0
        size = 0
        loop = True
        instance = ExtendedPluginClass('filesProcessing','', **plugin_info)
        # obtenemos los recursos
        while loop:
            status_template = _(u'Processing files. Step {step} of {total}')
            formatted_status = status_template.format(step=int(step + 1), total=int((total / 100) + 1))

            current_task.update_state(state='PROGRESS', meta={
                'status': formatted_status,
                'progress': step / total * 100,
                'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            })
            resources = list(mongodb.get_all_records('resources', filters, fields={'_id': 1}).limit(100).skip(step))
            resources = [str(resource['_id']) for resource in resources]

            records_filters = {
                'parent.id': {'$in': resources}
            }
            if body['overwrite']:
                records_filters = {"$or": [{"processing.fileProcessing": {"$exists": False}, **records_filters}, {"processing.fileProcessing": {"$exists": True}, **records_filters}]}
            else:
                records_filters['processing.fileProcessing'] = {'$exists': False}
            
            records = list(mongodb.get_all_records('records', records_filters, fields={'_id': 1, 'mime': 1, 'filepath': 1}))

            size += len(records)
            for file in records:
                try:
                    process_file(file, instance)
                except Exception as e:
                    print(str(e))

            step += 100
            if len(resources) < 100:
                loop = False

        instance.clear_cache()
        resp_template = _(u'Processed {size} files of a total of {total} resources.')
        formatted_resp = resp_template.format(size=int(size), total=int(total))
        return formatted_resp
        
      
    def get_settings(self):
        @self.route('/settings/<type>', methods=['GET'])
        @jwt_required()
        def get_settings(type):
            try:
                current_user = get_jwt_identity()
                
                self.validate_roles(current_user, ['admin', 'processing'])
                
                types = get_all_types()
                if isinstance(types, list):
                    types = tuple(types)[0]
                else:
                    types = types[0]

                current = self.get_plugin_settings()

                resp = {**self.settings}

                resp['settings'][1]['fields'] = [
                    {
                        'type': 'select',
                        'id': 'type',
                        'default': '',
                        'options': [{'value': t['slug'], 'label': t['name']} for t in types],
                        'required': True
                    },
                    {
                        'type': 'number',
                        'id': 'order',
                        'default': 0,
                        'required': True
                    }
                ]
                
                if current is None or current == {}:
                    resp['settings'][1]['default'] = []
                    
                else:
                    resp['settings'][1]['default'] = current['types_activation']

                
                if type == 'all':
                    return resp
                elif type == 'settings':
                    return resp['settings']
                else:
                    return resp['settings_' + type]
            except Exception as e:
                return {'msg': str(e)}, 500
            
        @self.route('/settings', methods=['POST'])
        @jwt_required()
        def set_settings_update():
            try:
                current_user = get_jwt_identity()

                self.validate_roles(current_user, ['admin', 'processing'])
                
                body = request.form.to_dict()
                data = body['data']
                data = json.loads(data)

                types = data['types_activation']
                for t in types:
                    if 'order' not in t:
                        t['order'] = '0'

                self.set_plugin_settings(data)

                return {'msg': _('Settings updated')}, 200
            
            except Exception as e:
                return {'msg': str(e)}, 500
        

plugin_info = {
    'name': 'Procesamiento de archivos',
    'description': 'Plugin para procesar archivos y generar versiones para consulta en el gestor documental',
    'version': '0.1',
    'author': 'Néstor Andrés Peña',
    'type': ['settings', 'bulk'],
    'actions': [
        {
            'placement': 'detail_resource',
            'label': 'Procesar archivos',
            'roles': ['admin', 'processing', 'editor'],
            'endpoint': 'bulk',
            'icon': 'PrecisionManufacturing',
            'extraOpts': [
                {
                    'type': 'checkbox',
                    'label': 'Sobreescribir archivos existentes',
                    'id': 'overwrite',
                    'instructions': 'Sobreescribir archivos ya procesados. Si esta opción está desactivada, el plugin solo procesará los archivos que no tengan una versión procesada.',
                    'default': False,
                    'required': False,
                }
            ]
        }
    ],
    'settings': {
        'settings': [
            {
                'type': 'instructions',
                'title': 'Instrucciones',
                'text': 'Este plugin permite procesar archivos y generar versiones para consulta en el gestor documental. Para ello, puede especificar el tipo de contenido sobre el cual quiere generar las versiones.',
            },
            {
                'type': 'multiple',
                'title': 'Tipos de contenido a generar',
                'id': 'types_activation',
                'fields': []
            }
        ],
        'settings_bulk': [
            {
                'type':  'instructions',
                'title': 'Instrucciones',
                'text': 'Este plugin permite procesar archivos y generar versiones para consulta en el gestor documental. Para ello, puede especificar el tipo de contenido sobre el cual quiere generar las versiones y los filtros que desea aplicar. Es importante notar que el proceso de generación de versiones puede tardar varios minutos, dependiendo de la cantidad de recursos que se encuentren en el gestor documental y el tamaño original de los archivos.',
            },
            {
                'type': 'checkbox',
                'label': 'Sobreescribir archivos existentes',
                'id': 'overwrite',
                'instructions': 'Sobreescribir archivos ya procesados. Si esta opción está desactivada, el plugin solo procesará los archivos que no tengan una versión procesada.',
                'default': False,
                'required': False,
            }
        ]
    }
}