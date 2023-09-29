# Nueva clase PluginClass que sirve para definir los plugins
#
class PluginClass:
    def __init__(self, name, description, version, author, category, icon, color, settings, actions):
        self.name = name
        self.description = description
        self.version = version
        self.author = author
        self.category = category
        self.icon = icon
        
    