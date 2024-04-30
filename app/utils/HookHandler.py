class HookHandler:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not isinstance(cls._instance, cls):
            cls._instance = super(HookHandler, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        self.hooks = {}

    def register(self, hook_name, func, queue=0):
        if hook_name not in self.hooks:
            self.hooks[hook_name] = []
        self.hooks[hook_name].append((queue, func))

    def call(self, hook_name, *args, **kwargs):
        if hook_name in self.hooks:
            for _, func in sorted(self.hooks[hook_name]):
                func(*args, **kwargs)