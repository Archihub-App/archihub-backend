class Hook:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not isinstance(cls._instance, cls):
            cls._instance = super(Hook, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        self.hooks = {}

    def register(self, hook_name, func):
        if hook_name not in self.hooks:
            self.hooks[hook_name] = []
        self.hooks[hook_name].append(func)

    def call(self, hook_name, *args, **kwargs):
        if hook_name in self.hooks:
            for func in self.hooks[hook_name]:
                func(*args, **kwargs)