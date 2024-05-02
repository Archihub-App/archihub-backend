class HookHandler:
    _instance = None
    _is_initialized = False

    def __new__(cls, *args, **kwargs):
        if not isinstance(cls._instance, cls):
            cls._instance = super(HookHandler, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        if not self._is_initialized:
            self._is_initialized = True
            self.hooks = {}

    def register(self, hook_name, func, queue=0):
        try:
            print("registering hook", hook_name, queue, func)
            if hook_name not in self.hooks:
                self.hooks[hook_name] = []
            self.hooks[hook_name].append((queue, func))
            print("registered hook", self.hooks)
        except Exception as e:
            print(str(e))

    def call(self, hook_name, *args, **kwargs):
        print("calling hook", hook_name, self.hooks)
        if hook_name in self.hooks:
            for _, func in sorted(self.hooks[hook_name]):
                func(*args, **kwargs)