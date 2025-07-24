import os
from app.api.aiservices.services import get_llm_models as get_ai_models

class AIHandler:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            
            cls._instance.start()
        return cls._instance
    
    def start(self):
        models, status = get_ai_models()
        self.models = models if isinstance(models, list) else []
        
    def get_models_with_capibilities(self, capabilities):
        if not self.models:
            return []
        
        filtered_models = []
        for model in self.models:
            if 'capabilities' in model and any(cap in model['capabilities'] for cap in capabilities):
                filtered_models.append(model)
        
        return filtered_models