import os
from app.api.aiservices.services import get_llm_models as get_ai_models
from app.api.aiservices.services import get_provider_models

class AIHandler:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            
            cls._instance.start()
        return cls._instance
    
    def start(self):
        models, status = get_ai_models()
        if status != 200:
            models = []
            
        for m in models:
            id = str(m['_id']['$oid'])
            m['models'] = get_provider_models(id)
            
        self.models = models if isinstance(models, list) else []
        
    def get_models_with_capabilities(self, capabilities):
        if not self.models:
            return []
        
        filtered_models = []
        for model in self.models:
            available_models = model.get('models', [])
            for m in available_models:
                if 'capabilities' in m and any(cap in m['capabilities'] for cap in capabilities):
                    filtered_models.append({
                        'id': model['_id']['$oid'],
                        'name': model['name'],
                        'model': m
                    })
        
        return filtered_models