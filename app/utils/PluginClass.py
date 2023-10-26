from flask import Blueprint, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.api.tasks.services import add_task
from app.api.users.services import has_role
from app.api.resources.services import update_cache
from app.api.records.services import update_cache as update_cache_records
from app.utils import DatabaseHandler
import os.path

mongodb = DatabaseHandler.DatabaseHandler()

class PluginClass(Blueprint):
    def __init__(self, path, filePath, import_name, name, description, version, author, type, settings=None):
        super().__init__(path, import_name)
        self.name = name
        self.description = description
        self.version = version
        self.author = author
        self.type = type
        self.filePath = filePath
        self.settings = settings
        
    def get_info(self):
        return {
            'name': self.name,
            'description': self.description,
            'version': self.version,
            'author': self.author,
            'type': self.type
        }
    
    def has_role(self, role, user):
        return has_role(user, role)
    
    def add_task_to_user(self, taskId, taskName, user, resultType):
        add_task(taskId, taskName, user, resultType)

    def update_cache(self, type):
        if type == 'resources':
            update_cache()
        elif type == 'records':
            update_cache_records()

    def get_image(self):
        @self.route('/image', methods=['GET'])
        @jwt_required()
        def get_img():
            try:
                current_user = get_jwt_identity()

                if not has_role(current_user, 'admin') and not has_role(current_user, 'processing'):
                    return {'msg': 'No tiene permisos suficientes'}, 401
                
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
                    return {'msg': 'No tiene permisos suficientes'}, 401
                
                return self.settings['settings_' + type]
            except Exception as e:
                return {'msg': str(e)}, 500

    