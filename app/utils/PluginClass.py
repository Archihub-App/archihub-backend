from flask import Blueprint

class PluginClass(Blueprint):
    def __init__(self, path, import_name, name, description, version, author):
        super().__init__(path, import_name)
        self.name = name
        self.description = description
        self.version = version
        self.author = author
        
    def get_info(self):
        return {
            'name': self.name,
            'description': self.description,
            'version': self.version,
            'author': self.author
        }
    