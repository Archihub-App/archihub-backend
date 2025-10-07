from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required, get_jwt_identity
from celery import shared_task
from flask import request, send_file
from app.utils import DatabaseHandler
from app.api.types.services import get_by_slug
from app.api.resources.services import get_value_by_path
from app.utils.functions import cache_type_roles
from app.api.resources.services import get_accessRights
import os
import uuid
import pandas as pd
from dotenv import load_dotenv
from bson.objectid import ObjectId
load_dotenv()

mongodb = DatabaseHandler.DatabaseHandler()
USER_FILES_PATH = os.environ.get('USER_FILES_PATH', '')
WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')

class ExtendedPluginClass(PluginClass):
    def __init__(self, path, import_name, name, description, version, author, type, settings, actions=None, capabilities=None, **kwargs):
        super().__init__(path, __file__, import_name, name, description, version, author, type, settings, actions=None, capabilities=None, **kwargs)

    def add_routes(self):
        @self.route('/public/downloadInventory', methods=['POST'])
        def download_inventory():
            try:
                body = request.get_json()
                
                if 'post_type' not in body:
                    return {'msg': 'No se especificó el tipo de contenido'}, 400
                if 'view' not in body:
                    return {'msg': 'No se especificó la vista'}, 400
                
                view = mongodb.get_record('views', {'slug': body['view']})
                
                if not view:
                    return {'msg': 'Vista no existe'}, 404
                
                view_post_type = view['visible']
                
                if isinstance(body['post_type'], list):
                    for pt in body['post_type']:
                        if pt not in view_post_type:
                            return {'msg': 'No tiene permisos para obtener un recurso'}, 401
                elif body['post_type'] == '*':
                    body['post_type'] = view_post_type
                elif isinstance(body['post_type'], str):
                    if body['post_type'] not in view_post_type:
                        return {'msg': 'No tiene permisos para obtener un recurso'}, 401
                    body['post_type'] = [body['post_type']]
                    
                filters = {
                    'post_type': {'$in': body['post_type']}
                }
                
                if view['root']:
                    if view['parent']:
                        filters['parent'] = view['parent']
                        
                from .services import generateResourceInventory
                path = os.path.join(WEB_FILES_PATH, 'inventoryMaker')
                filename = 'public_'+body['view']+'-'.join(body['post_type'])
                file = generateResourceInventory(filters, None, path, filename)
                return send_file(file, as_attachment=True)
            except Exception as e:
                print(str(e))
                return {'msg': str(e)}, 500
        
        @self.route('/bulk', methods=['POST'])
        @jwt_required()
        def create_inventory():
            current_user = get_jwt_identity()
            body = request.get_json()

            self.validate_roles(current_user, ['admin', 'processing', 'editor'])
            self.validate_fields(body, 'bulk')
            
            post_type = body['post_type']
            for p in post_type:
                post_type_roles = cache_type_roles(p)
                if post_type_roles['viewRoles']:
                    canView = False
                    for r in post_type_roles['viewRoles']:
                        if self.has_role(current_user, r) or self.has_role(current_user, 'admin'):
                            canView = True
                            break
                    if not canView:
                        return {'msg': 'No tiene permisos para obtener un recurso'}, 401
                
            if 'parent' in body:
                if body['parent']:
                    accessRights = get_accessRights(body['parent']['id'])
                    if accessRights:
                        if not self.has_right(current_user, accessRights['id']) and not self.has_role(current_user, 'admin'):
                            return {'msg': 'No tiene permisos para acceder al recurso'}, 401
            
            task = self.create.delay(body, current_user)
            self.add_task_to_user(task.id, 'inventoryMaker.create_inventory', current_user, 'file_download')

            return {'msg': 'Se agregó la tarea a la fila de procesamientos. Puedes revisar en tu perfil cuando haya terminado y descargar el inventario.'}, 201
        
        @self.route('/bulk-lists', methods=['POST'])
        @jwt_required()
        def create_inventory_lists():
            # get the current user
            current_user = get_jwt_identity()
            body = request.get_json()

            if not self.has_role('admin', current_user) and not self.has_role('editor', current_user):
                return {'msg': 'No tiene permisos suficientes'}, 401
            
            if 'parent' not in body:
                return {'msg': 'No se especificó el listado'}, 400

            task = self.create_lists.delay(body, current_user)
            self.add_task_to_user(task.id, 'inventoryMaker.create_inventory_lists', current_user, 'file_download')

            return {'msg': 'Se agregó la tarea a la fila de procesamientos. Puedes revisar en tu perfil cuando haya terminado y descargar el inventario.'}, 201
        
        @self.route('/bulk-forms', methods=['POST'])
        @jwt_required()
        def create_inventory_forms():
            # get the current user
            current_user = get_jwt_identity()
            body = request.get_json()

            if not self.has_role('admin', current_user) and not self.has_role('editor', current_user):
                return {'msg': 'No tiene permisos suficientes'}, 401
            
            if 'parent' not in body:
                return {'msg': 'No se especificó el estándar de metadatos'}, 400

            task = self.create_forms.delay(body, current_user)
            self.add_task_to_user(task.id, 'inventoryMaker.create_inventory_forms', current_user, 'file_download')

            return {'msg': 'Se agregó la tarea a la fila de procesamientos. Puedes revisar en tu perfil cuando haya terminado y descargar el inventario.'}, 201
        
        @self.route('/bulk-types', methods=['POST'])
        @jwt_required()
        def create_inventory_types():
            # get the current user
            current_user = get_jwt_identity()
            body = request.get_json()

            if not self.has_role('admin', current_user) and not self.has_role('editor', current_user):
                return {'msg': 'No tiene permisos suficientes'}, 401
            
            task = self.create_types.delay(body, current_user)
            self.add_task_to_user(task.id, 'inventoryMaker.create_inventory_types', current_user, 'file_download')

            return {'msg': 'Se agregó la tarea a la fila de procesamientos. Puedes revisar en tu perfil cuando haya terminado y descargar el inventario.'}, 201
        

        @self.route('/filedownload/<taskId>', methods=['GET'])
        @jwt_required()
        def file_download(taskId):
            current_user = get_jwt_identity()

            if not self.has_role('admin', current_user) and not self.has_role('processing', current_user) and not self.has_role('editor', current_user):
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
        
    @shared_task(ignore_result=False, name='inventoryMaker.create_inventory_forms')
    def create_forms(body, user):
        def clean_string(input_string):
            if input_string is None:
                return ''
            cleaned_string = ''.join(char for char in input_string if ord(char) > 31 or ord(char) in (9, 10, 13))
            return cleaned_string

        forms = list(mongodb.get_all_records('forms', {'slug': body['parent']}))

        forms_df = []
        fields_df = []

        obj = {}

        obj['id'] = 'id'
        obj['name'] = 'name'
        obj['slug'] = 'slug'
        obj['description'] = 'description'

        forms_df.append(obj)

        for f in forms:
            fields = f['fields']

            obj = {
                'id': str(f['_id']),
                'name': f['name'],
                'description': f['description'],
                'slug': f['slug']
            }

            forms_df.append(obj)

            for field in fields:
                obj = {
                    'label': field['label'],
                    'type': field['type'],
                    'destiny': field['destiny'],
                    'required': field['required'],
                    'instructions': field['instructions'] if 'instructions' in field else '',
                }

                fields_df.append(obj)

        folder_path = USER_FILES_PATH + '/' + user + '/inventoryMaker'
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

        file_id = str(uuid.uuid4())

        with pd.ExcelWriter(folder_path + '/' + file_id + '.xlsx') as writer:
            df = pd.DataFrame(forms_df)
            df.to_excel(writer, sheet_name='Estandar', index=False)
            df = pd.DataFrame(fields_df)
            df.to_excel(writer, sheet_name='Campos', index=False)

        return '/' + user + '/inventoryMaker/' + file_id + '.xlsx'

    @shared_task(ignore_result=False, name='inventoryMaker.create_inventory_lists')
    def create_lists(body, user):
        def clean_string(input_string):
            if input_string is None:
                return ''
            cleaned_string = ''.join(char for char in input_string if ord(char) > 31 or ord(char) in (9, 10, 13))
            return cleaned_string
    
        lists = list(mongodb.get_all_records('lists', {'_id': ObjectId(body['parent'])}))

        lists_df = []

        obj = {}

        obj['id'] = 'id'
        obj['name'] = 'name'
        obj['description'] = 'description'
        obj['options'] = 'options'

        lists_df.append(obj)

        for l in lists:
            options = list(mongodb.get_all_records('options', {'_id': {'$in': [ObjectId(option) for option in l['options']]}}, [('term', 1)]))

            obj = {
                'id': str(l['_id']),
                'name': l['name'],
                'description': l['description'],
                'options': ', '.join([str(option['term']) for option in options])
            }

            lists_df.append(obj)

        folder_path = USER_FILES_PATH + '/' + user + '/inventoryMaker'
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

        file_id = str(uuid.uuid4())

        with pd.ExcelWriter(folder_path + '/' + file_id + '.xlsx') as writer:
            df = pd.DataFrame(lists_df)
            df.to_excel(writer, sheet_name='Listado', index=False)

        return '/' + user + '/inventoryMaker/' + file_id + '.xlsx'
        
    @shared_task(ignore_result=False, name='inventoryMaker.create_inventory_types')
    def create_types(body, user):
        def clean_string(input_string):
            if input_string is None:
                return ''
            cleaned_string = ''.join(char for char in input_string if ord(char) > 31 or ord(char) in (9, 10, 13))
            return cleaned_string

        types = list(mongodb.get_all_records('post_types'))

        types_df = []

        obj = {}

        obj['id'] = 'id'
        obj['name'] = 'name'
        obj['slug'] = 'slug'
        obj['description'] = 'description'
        obj['metadata'] = 'metadata'
        obj['icon'] = 'icon'
        obj['hierarchical'] = 'hierarchical'
        obj['parentType'] = 'parentType'
        obj['editRoles'] = 'editRoles'
        obj['viewRoles'] = 'viewRoles'

        types_df.append(obj)

        for t in types:
            obj = {
                'id': str(t['_id']),
                'name': t['name'],
                'slug': t['slug'],
                'description': t['description'],
                'metadata': t['metadata'],
                'icon': t['icon'],
                'hierarchical': t['hierarchical'],
                'parentType': t['parentType'],
                'editRoles': t['editRoles'],
                'viewRoles': t['viewRoles']
            }

            types_df.append(obj)

        folder_path = USER_FILES_PATH + '/' + user + '/inventoryMaker'
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

        file_id = str(uuid.uuid4())

        with pd.ExcelWriter(folder_path + '/' + file_id + '.xlsx') as writer:
            df = pd.DataFrame(types_df)
            df.to_excel(writer, sheet_name='Tipo', index=False)

        return '/' + user + '/inventoryMaker/' + file_id + '.xlsx'
    
    @shared_task(ignore_result=False, name='inventoryMaker.create_inventory')
    def create(body, user):
        def clean_string(input_string):
            if input_string is None:
                return ''
            cleaned_string = ''.join(char for char in input_string if ord(char) > 31 or ord(char) in (9, 10, 13))
            return cleaned_string

        filters = {
            'post_type': {'$in': body['post_type']}
        }

        type_metadata = []
        for p in body['post_type']:
            type = get_by_slug(p)
            if type and 'metadata' in type and 'fields' in type['metadata']:
                type_metadata.extend(type['metadata']['fields'])
            
        unique = {}
        for f in type_metadata:
            dest = f.get('destiny')
            if dest is None:
                continue
            if dest not in unique:
                unique[dest] = f
        type_metadata = list(unique.values())

        resources_df = []
        records_df = []

        if 'parent' in body:
            if body['parent']:
                filters = {'$or': [{'parents.id': body['parent']['id'], 'post_type': {'$in': body['post_type']}}, {'_id': ObjectId(body['parent']['id'])}]}

                if 'status' not in body:
                    for o in filters['$or']:
                        o['status'] = 'published'
                elif body['status'] == 'draft':
                    for o in filters['$or']:
                        o['status'] = 'draft'

                    from app.api.users.services import has_role
                    if not has_role(user, 'publisher') or not has_role(user, 'admin'):
                        for o in filters['$or']:
                            o['createdBy'] = user

        if 'status' not in body:
            filters['status'] = 'published'
        elif body['status'] == 'draft':
            filters['status'] = 'draft'

            from app.api.users.services import has_role
            if not has_role(user, 'publisher') or not has_role(user, 'admin'):
                filters['createdBy'] = user
        elif body['status'] == 'published':
            filters['status'] = 'published'
            
        # buscamos los recursos con los filtros especificados
        resources = list(mongodb.get_all_records('resources', filters))

        # si no hay recursos, retornamos un error
        if len(resources) == 0:
            raise Exception('No se encontraron recursos con los filtros especificados')
        
        obj = {}

        obj['Tipo de contenido'] = 'post_type'
        obj['id'] = 'id'
        obj['ident'] = 'ident'

        type_metadata = [f for f in type_metadata if f['type'] != 'file']

        for f in type_metadata:
            obj[f['label']] = f['destiny']

        resources_df.append(obj)


        # si hay recursos, iteramos
        for r in resources:
            obj = {}
            
            obj['id'] = str(r['_id'])
            obj['ident'] = r['ident']
            obj['Tipo de contenido'] = r['post_type']

            for f in type_metadata:
                if f['type'] == 'text' or f['type'] == 'text-area':
                    obj[f['label']] = clean_string(get_value_by_path(r, f['destiny']))
                elif f['type'] == 'select':
                    obj[f['label'] + '_id'] = clean_string(get_value_by_path(r, f['destiny']))
                    
                    if obj[f['label'] + '_id']:
                        option = mongodb.get_record('options', {'_id': ObjectId(obj[f['label'] + '_id'])})
                        if option:
                            obj[f['label']] = option['term']
                elif f['type'] == 'select-multiple2':
                    obj[f['label'] + '_ids'] = ', '.join([str(o) for o in get_value_by_path(r, f['destiny'])]) if get_value_by_path(r, f['destiny']) else ''
                    if obj[f['label'] + '_ids']:
                        options = list(mongodb.get_all_records('options', {'_id': {'$in': [ObjectId(o) for o in get_value_by_path(r, f['destiny'])]}}))
                        if options:
                            obj[f['label']] = ', '.join([o['term'] for o in options]) if options else ''
                elif f['type'] == 'simple-date':
                    date = get_value_by_path(r, f['destiny'])
                    if date:
                        obj[f['label']] = date.strftime('%Y-%m-%d')
                    else:
                        obj[f['label']] = ''
                elif f['type'] == 'number':
                    obj[f['label']] = get_value_by_path(r, f['destiny'])

            resources_df.append(obj)

            r_ = list(mongodb.get_all_records('records', {'parent.id': str(r['_id'])}, fields={'name': 1, 'displayName': 1}))

            files = [record['name'] for record in r_]
            obj['files'] = ', '.join(files)

            files_ids = [str(record['_id']) for record in r_]
            obj['files_ids'] = ', '.join(files_ids)

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
    'description': 'Plugin para exportar inventarios de los recursos y del contenido del gestor documental.',
    'version': '0.2',
    'author': 'Néstor Andrés Peña',
    'type': ['bulk'],
    'settings': {
        'settings_bulk': [
            {
                'type':  'instructions',
                'title': 'Instrucciones',
                'text': 'Este plugin permite generar inventarios en archivo excel del contenido del gestor documental. Para ello, puede especificar el tipo de contenido sobre el cual quiere generar el inventario y los filtros que desea aplicar. El archivo se encontrará en su perfil para su descarga una vez se haya terminado de generar. Es importante notar que el proceso de generación de inventarios puede tardar varios minutos, dependiendo de la cantidad de recursos que se encuentren en el gestor documental.',
            }
        ],
        'settings_lunch': [
            {

            }
        ]
    }
}