from app.utils import DatabaseHandler, CacheHandler
from cryptography.fernet import Fernet
from config import config
import os
import json
from bson import json_util
from bson.objectid import ObjectId
from app.api.llms.models import LlmProvider, LlmProviderUpdate
from app.api.llms.utils.LLMProviders import OpenAIProvider

PROVIDER_CLASSES = {
    'OpenAI': OpenAIProvider,
}

fernet_key = config[os.environ['FLASK_ENV']].FERNET_KEY
mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()
fernet = Fernet(fernet_key)

def parse_result(result):
    return json.loads(json_util.dumps(result))

def update_cache():
    get_llm_models.invalidate_all()
    get_llm_providers.invalidate_all()

@cacheHandler.cache.cache()
def get_llm_models():
    try:
        llm_models = list(mongodb.get_all_records("llm_models", fields={"key": 0}))
        llm_models = parse_result(llm_models)
        return llm_models, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
@cacheHandler.cache.cache()
def get_llm_providers():
    try:
        from .utils.LLMProviders import get_llm_providers
        providers = get_llm_providers()
        return providers, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
def create_llm_model(model):
    try:
        from .utils.LLMProviders import get_llm_providers
        providers = get_llm_providers()
        if model['provider'] not in providers:
            return {'msg': 'Proveedor no encontrado'}, 404
        model['key'] = fernet.encrypt(model['key'].encode()).decode()
        model = LlmProvider(**model)
        mongodb.insert_record("llm_models", model)
        update_cache()
        return {'msg': 'Modelo creado exitosamente'}, 201
    except Exception as e:
        return {'msg': str(e)}, 500
    
def update_llm_model(model_id, model):
    try:
        model['key'] = fernet.encrypt(model['key'].encode()).decode()
        model = LlmProviderUpdate(**model)
        mongodb.update_record("llm_models", {"_id": ObjectId(model_id)}, model)
        update_cache()
        return {'msg': 'Modelo actualizado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
def delete_llm_model(model_id):
    try:
        mongodb.delete_record("llm_models", {"_id": ObjectId(model_id)})
        update_cache()
        return {'msg': 'Modelo eliminado exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
def get_provider_class(id):
    llm_provider = mongodb.get_record("llm_models", filters={"_id": ObjectId(id)})
    if not llm_provider:
        raise Exception('Modelo no encontrado')
    
    llm_provider.pop('_id')
    
    provider_class = PROVIDER_CLASSES.get(llm_provider['provider'])
    if not provider_class:
        raise Exception('Proveedor no encontrado')
    
    llm_provider.pop('provider')
    provider = provider_class(**llm_provider)
    return provider
    
def get_provider_models(id):
    try:
        provider = get_provider_class(id)
        response = provider.getModels()
        return response, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
def set_conversation(data, user):
    try:
        provider = get_provider_class(data['provider']['id'])
        if data['type'] == 'transcription':
            from .utils.TranscriptionProcessing import create_transcription_conversation
            response = create_transcription_conversation(data, provider, user)
            return response, 200
        return 'ok', 200
    except Exception as e:
        return {'msg': str(e)}, 500