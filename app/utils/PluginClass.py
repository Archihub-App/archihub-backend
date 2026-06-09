from flask import Blueprint, send_file, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from flask_babel import gettext as _
from app.api.tasks.services import add_task, has_task
from app.api.users.services import has_role
from app.api.system.models import OptionUpdate
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from app.utils import HookHandler
from copy import deepcopy
import uuid
import os.path
import requests
import json
import datetime
from bson.objectid import ObjectId
mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()
hookHandler = HookHandler.HookHandler()

TEMPORAL_FILES_PATH = os.environ.get('TEMPORAL_FILES_PATH', '')
CLEAR_CACHE_PATH = os.environ.get('MASTER_HOST', '') + '/system/node-clear-cache'
NODE_TOKEN = os.environ.get('NODE_TOKEN', '')

class PluginClass(Blueprint):
    def __init__(self, path, filePath, import_name, name, description, version, author, type, settings=None, capabilities=None, actions=None, **kwargs):
        super().__init__(path, import_name)
        self.name = name
        self.description = description
        self.version = version
        self.author = author
        self.type = type
        self.filePath = filePath
        self.path = path
        self.settings = settings
        self.capabilities = capabilities
        self.actions = actions
        self.slug = path.replace('app.plugins.', '')

    def _translate_display_node(self, value, parent_key=None):
        translatable_keys = {'name', 'label', 'title', 'text', 'description', 'placeholder'}

        if isinstance(value, dict):
            translated = {}
            for key, item in value.items():
                translated[key] = self._translate_display_node(item, key)
            return translated

        if isinstance(value, list):
            return [self._translate_display_node(item, parent_key) for item in value]

        if isinstance(value, str) and parent_key in translatable_keys:
            return _(value)

        return value

    def get_translated_settings(self):
        if not self.settings:
            return self.settings

        return self._translate_display_node(deepcopy(self.settings))

    def get_translated_actions(self):
        if not self.actions:
            return self.actions

        return self._translate_display_node(deepcopy(self.actions))

    def get_translated_info(self):
        info = {
            'name': self.name,
            'description': self.description,
            'version': self.version,
            'author': self.author,
            'type': self.type
        }

        return self._translate_display_node(info)

    def activate_settings(self):
        pass
    
    def get_capabilities(self):
        return self.capabilities
    
    def get_actions(self):
        return self.get_translated_actions()
        
    def get_info(self):
        return self.get_translated_info()
    
    def has_role(self, role, user):
        return has_role(user, role)
    
    def add_task_to_user(self, taskId, taskName, user, resultType, params={}):
        add_task(taskId, taskName, user, resultType, params=params)

    def has_task(self, taskName, user):
        return has_task(user, taskName)

    def allowedFile(self, filename, allowed_extensions):
        return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions
           
    @classmethod
    def update_data(cls, collection, id, update):
        try:
            if collection == 'records':
                from app.api.records.services import update_record_by_id
                resp = update_record_by_id(id, None, update)
            elif collection == 'resources':
                from app.api.resources.models import ResourceUpdate
                from app.api.resources.services import get_metadata, validate_fields, update_relations_children
                metadata = get_metadata(update['post_type'])
                
                errors = {}
                
                body = validate_fields(update, metadata, errors)
                
                body['updatedAt'] = datetime.datetime.now()
                body['updatedBy'] = 'system'
                
                update_relations_children(update, metadata['fields'])
                
                if errors:
                    return errors, 400
                
                update = ResourceUpdate(**body)
                
                resp = mongodb.update_record(collection, {'_id': ObjectId(id)}, update)
                
            cls.clear_cache()
            return resp
        except Exception as e:
            raise Exception(str(e))
    
    def save_temp_file(self, file, filename):
        filename_new = str(uuid.uuid4()) + '.' + filename.rsplit('.', 1)[1].lower()
        
        path = os.path.join(TEMPORAL_FILES_PATH)
        if not os.path.exists(path):
                    os.makedirs(path)

        file.save(os.path.join(path, filename))
        file.flush()
        os.fsync(file.fileno())

        os.rename(os.path.join(path, filename),
                            os.path.join(path, filename_new))
        
        return filename_new
    
    def validate_fields(self, body, slug):
        if 'post_type' not in body and slug == 'bulk':
            return {'msg': _('No content type was specified')}, 400
        
        settings = self.settings['settings_' + slug]
        for setting in settings:
            if 'required' not in setting:
                setting['required'] = False
            else:
                if setting['required'] and setting['id'] not in body:
                    return {'msg': _('The field {label} is required', label=setting['label'])}, 400
                if setting['type'] == 'file' and setting['required'] and len(body[setting['id']]) == 0:
                    return {'msg': _('The field {label} is required', label=setting['label'])}, 400
                
    def validate_roles(self, user, roles):
        temp = []
        for role in roles:
            if has_role(user, role):
                temp.append(role)
        
        if len(temp) == 0:
            return {'msg': _('You do not have sufficient permissions')}, 401
        
    
    def get_plugin_settings(self):
        settings = mongodb.get_record('system', {'name': 'active_plugins'}, fields={'plugins_settings': 1})
        if 'plugins_settings' not in settings:
            return {}
        elif self.slug not in settings['plugins_settings']:
            return {}
        else:
            return settings['plugins_settings'][self.slug]
        
    def set_plugin_settings(self, settings):
        settings_old = mongodb.get_record('system', {'name': 'active_plugins'}, fields={'plugins_settings': 1})
        if 'plugins_settings' not in settings_old:
            settings_old['plugins_settings'] = {}
        
        settings_old['plugins_settings'][self.slug] = settings
        
        update = OptionUpdate(**settings_old)
        mongodb.update_record('system', {'name': 'active_plugins'}, update)
    
    @classmethod
    def clear_cache(self):
        try:
            headers = {
                 'Authorization': 'Bearer ' + NODE_TOKEN
            }
            requests.get(CLEAR_CACHE_PATH, headers=headers)
        except:
            pass

    def get_image(self):
        @self.route('/image', methods=['GET'])
        @jwt_required()
        def get_img():
            try:
                current_user = get_jwt_identity()

                if not has_role(current_user, 'admin') and not has_role(current_user, 'processing'):
                    return {'msg': _('You do not have sufficient permissions')}, 401
                
                path = os.path.dirname(os.path.abspath(self.filePath)) + '/static/image.png'
                return send_file(path, mimetype='image/png')
            except Exception as e:
                    return {'msg': str(e)}, 500
        
    def get_settings(self):
        @self.route('/settings/<type>', methods=['GET'])
        @jwt_required()
        def get_settings(type):
            try:
                current_user = get_jwt_identity()

                if not has_role(current_user, 'admin') and not has_role(current_user, 'processing'):
                    return {'msg': _('You do not have sufficient permissions')}, 401

                translated_settings = self.get_translated_settings()
                
                if type == 'all':
                    return translated_settings
                elif type == 'settings':
                    return translated_settings['settings']
                else:
                    return translated_settings['settings_' + type]
            except Exception as e:
                return {'msg': str(e)}, 500
            
        @self.route('/settings', methods=['POST'])
        @jwt_required()
        def set_settings_update():
            try:
                current_user = get_jwt_identity()

                if not has_role(current_user, 'admin') and not has_role(current_user, 'processing'):
                    return {'msg': _('You do not have sufficient permissions')}, 401
                
                body = request.form.to_dict()
                data = body['data']
                data = json.loads(data)

                print(data)

                self.set_plugin_settings(data)
                return {'msg': _('Settings saved')}, 200
            
            except Exception as e:
                return {'msg': str(e)}, 500

    