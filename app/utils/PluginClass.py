from flask import Blueprint
from app.api.tasks.services import add_task

class PluginClass(Blueprint):
    def __init__(self, path, import_name, name, description, version, author, type):
        super().__init__(path, import_name)
        self.name = name
        self.description = description
        self.version = version
        self.author = author
        self.type = type
        
    def get_info(self):
        return {
            'name': self.name,
            'description': self.description,
            'version': self.version,
            'author': self.author,
            'type': self.type
        }
    
    def add_task_to_user(self, taskId, user):
        print(f'Adding task {taskId} to user {user}')