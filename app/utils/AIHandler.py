import os
from app.utils import DatabaseHandler
from app.api.aiservices.services import get_llm_models as get_ai_models
from app.api.aiservices.services import get_provider_models
from app.api.aiservices.utils.ModelsProviders import OpenAIProvider, GoogleProvider, AzureProvider

mongodb = DatabaseHandler.DatabaseHandler()

class AIHandler:
    _instance = None
    
    PROVIDER_CLASSES = {
        'OpenAI': OpenAIProvider,
        'Google': GoogleProvider,
        'Azure': AzureProvider
    }
    
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
            provider_models, provider_status = get_provider_models(id)
            if provider_status == 200:
                m['models'] = provider_models
            else:
                m['models'] = []
            
        self.models = models if isinstance(models, list) else []
        
    def get_models_with_capabilities(self, capabilities):
        if not self.models:
            return []
        
        filtered_models = []
        for model in self.models:
            available_models = model.get('models', [])
            for m in available_models:
                model_caps = m.get('capabilities')
                if model_caps and isinstance(model_caps, list) and any(cap in model_caps for cap in capabilities):
                    filtered_models.append({
                        'id': model['_id']['$oid'],
                        'name': model['name'],
                        'model': m
                    })
        
        return filtered_models
    
    def get_provider_class(self, filters):
        llm_provider = mongodb.get_record("llm_models", filters=filters)
        if not llm_provider:
            raise Exception('Modelo no encontrado')
        llm_provider.pop('_id')
        
        provider_class = self.PROVIDER_CLASSES.get(llm_provider['provider'])
        if not provider_class:
            raise Exception('Proveedor no encontrado')
        llm_provider.pop('provider')
        provider = provider_class(**llm_provider)
        
        return provider
    
    def call_model(self, model):
        if not self.models:
            raise Exception('No AI models available')
        
        print(f"Calling model: {model}")
        
        provider = self.get_provider_class({'name': model['provider']})
        
        
        # raise Exception('Model not found')