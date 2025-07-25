from celery import chain
from app.api.tasks.services import add_task

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

    def register(self, hook_name, func, args=None, kwargs=None, queue=0):
        try:
            if hook_name not in self.hooks:
                self.hooks[hook_name] = []
            
            reg_args = args if args is not None else []
            reg_kwargs = kwargs if kwargs is not None else {}

            is_instance_method = hasattr(func, '__self__') and hasattr(func, '__func__')

            for _, f, a, k in self.hooks[hook_name]:
                is_f_instance_method = hasattr(f, '__self__') and hasattr(f, '__func__')
                if is_instance_method and is_f_instance_method:
                    # Prevent registering if a method from the same class is already registered.
                    if f.__func__ == func.__func__ and type(f.__self__) is type(func.__self__) and a == reg_args and k == reg_kwargs:
                        return
                elif f == func and a == reg_args and k == reg_kwargs:
                    return
            
            self.hooks[hook_name].append((queue, func, reg_args, reg_kwargs))
        except Exception as e:
            print(str(e))

    def call(self, hook_name, *additional_args, **additional_kwargs):
        if hook_name in self.hooks:
            task_signatures = []
            task_data = []
            names = []
            task_ids = []
            
            sync_return_value = additional_args[0] if additional_args else None
            
            for _, func, reg_args, reg_kwargs in sorted(self.hooks[hook_name], key=lambda x: x[0]):
                if not isinstance(reg_args, list):
                    reg_args = [reg_args] if reg_args is not None else []
                if not isinstance(reg_kwargs, dict):
                    reg_kwargs = {}
                if not isinstance(additional_kwargs, dict):
                    additional_kwargs = {}

                final_args = list(reg_args) + list(additional_args)
                final_kwargs = {**reg_kwargs, **additional_kwargs}

                if hasattr(func, 'si'):  # It's a Celery task
                    final_args = list(reg_args) + list(additional_args)
                    task_signature = func.si(*final_args, **final_kwargs)
                    funcname = func.name

                    names.append(funcname)
                    task_signatures.append(task_signature)
                    task_data.append((funcname, final_args, final_kwargs))
                else:  # It's a regular synchronous function
                    final_args = list(reg_args) + list(additional_args)
                    sync_return_value = func(*final_args, **final_kwargs)
            
            if task_signatures:
                result = chain(*task_signatures).apply_async()
                task_ids = self.get_task_ids(result)
                temp = []
                for x, task_id in enumerate(task_ids):
                    if task_id not in temp:
                        temp.append(task_id)
                        funcname, final_args, final_kwargs = task_data[x]
                        add_task(task_id, names[x], 'automatic', 'hook')
            
            return sync_return_value

    def get_task_ids(self, result):
        ids = []
        while result is not None:
            ids.append(result.id)
            result = getattr(result, 'parent', None)
        return list(reversed(ids))