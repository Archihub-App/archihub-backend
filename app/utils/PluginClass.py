from flask import Blueprint, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.api.tasks.services import add_task
from app.api.users.services import has_role
import os.path

class PluginClass(Blueprint):
    def __init__(self, path, filePath, import_name, name, description, version, author, type):
        super().__init__(path, import_name)
        self.name = name
        self.description = description
        self.version = version
        self.author = author
        self.type = type
        self.filePath = filePath
        
    def get_info(self):
        return {
            'name': self.name,
            'description': self.description,
            'version': self.version,
            'author': self.author,
            'type': self.type
        }
    
    def add_task_to_user(self, taskId, taskName, user, resultType):
        add_task(taskId, taskName, user, resultType)

    def get_image(self):
        @self.route('/image', methods=['GET'])
        @jwt_required()
        def get_img():
            current_user = get_jwt_identity()

            if not has_role(current_user, 'admin'):
                return {'msg': 'No tiene permisos suficientes'}, 401
            
            path = os.path.dirname(os.path.abspath(self.filePath)) + '/static/image.png'
            return send_file(path, mimetype='image/png')

    