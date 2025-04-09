from app.utils import DatabaseHandler, CacheHandler
from cryptography.fernet import Fernet
from config import config
import os
import json
from bson import json_util
from bson.objectid import ObjectId
from app.api.llms.models import LlmProvider, LlmProviderUpdate

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