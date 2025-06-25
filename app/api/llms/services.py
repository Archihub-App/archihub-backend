from app.utils import DatabaseHandler, CacheHandler
from cryptography.fernet import Fernet
from config import config
import os
import json
from bson import json_util
from bson.objectid import ObjectId
from app.api.llms.models import LlmProvider, LlmProviderUpdate
from app.api.llms.utils.LLMProviders import OpenAIProvider, GoogleProvider, AzureProvider

PROVIDER_CLASSES = {
    'OpenAI': OpenAIProvider,
    'Google': GoogleProvider,
    'Azure': AzureProvider
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
    
def get_llm_model(model_id):
    try:
        llm_model = mongodb.get_record("llm_models", filters={"_id": ObjectId(model_id)}, fields={"key": 0})
        if not llm_model:
            return {'msg': 'Modelo no encontrado'}, 404
        llm_model = parse_result(llm_model)
        return llm_model, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
def update_llm_model(model_id, model):
    try:
        provider = model.get('provider')
        if provider != 'Azure':
            endpoint = model.get('endpoint')
            endpointCognitive = model.get('endpointCognitive')
            if endpoint or endpointCognitive:
                model.pop('endpoint', None)
                model.pop('endpointCognitive', None)
                
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
    llm_provider = mongodb.get_record("llm_models", filters={"_id": ObjectId(id)}, fields={"endpoint": 0})
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
        print("Setting conversation with data:", data)
        provider = get_provider_class(data['provider']['id'])
        if data['type'] == 'transcription':
            from .utils.TranscriptionProcessing import create_transcription_conversation
            response = create_transcription_conversation(data, provider, user)
            return response, 200
        elif data['type'] == 'document':
            from .utils.DocumentProcessing import create_document_conversation
            response = create_document_conversation(data, provider, user)
            return response, 200
        elif data['type'] == 'image_gallery':
            from .utils.ImageProcessing import create_image_gallery_conversation
            response = create_image_gallery_conversation(data, provider, user)
            return response, 200
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500

def get_conversation_history(data, user):
    type = data['type']
    id = data['id']
    
    try:
        if type == 'record':
            from app.api.records.services import get_by_id
            resp_, status = get_by_id(id, user)
            if status != 200:
                raise Exception('Error al obtener el record')
            
            conversations = list(mongodb.get_all_records('conversations', {'record_id': id, 'user': user}, fields={'_id': 1, 'created_at': 1, 'messages': 1, 'updated_at': 1}, sort=[('updated_at', -1)]))
            conversations = parse_result(conversations)
            for c in conversations:
                c['messages'] = [c['messages'][0]]
            return conversations, 200
        elif type == 'image_gallery':
            conversations = list(mongodb.get_all_records('conversations', {'resource_id': id, 'user': user}, fields={'_id': 1, 'created_at': 1, 'messages': 1, 'updated_at': 1}, sort=[('updated_at', -1)]))
            conversations = parse_result(conversations)
            for c in conversations:
                c['messages'] = [process_img_message_content(c['messages'][0])]
            return conversations, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
def get_conversation(id, user):
    try:
        conversations = mongodb.get_record('conversations', {'_id': ObjectId(id), 'user': user}, fields={'_id': 1, 'created_at': 1, 'messages': 1, 'updated_at': 1, 'resource_id': 1, 'page': 1})
        if not conversations:
            return {'msg': 'Conversación no encontrada'}, 404
        
        conversations = parse_result(conversations)
        
        # Track the last image that was included
        last_included_image_path = None
        
        for i in range(len(conversations['messages'])):
            message = conversations['messages'][i]
            
            if message['role'] == 'user' and isinstance(message.get('content'), list):
                # Check if this message contains an image
                has_image = False
                current_image_path = None
                
                for content_item in message['content']:
                    if content_item.get('type') == 'image_path':
                        has_image = True
                        current_image_path = content_item.get('path')
                        break
                
                # If this message has an image and it's the same as the last one, remove it
                if has_image and current_image_path == last_included_image_path:
                    # Remove the image from this message, keep only text content
                    message['content'] = [item for item in message['content'] if item.get('type') != 'image_path']
                elif has_image:
                    # This is a new image, update the last included image path
                    last_included_image_path = current_image_path
            
            # Process the message (convert image paths to base64)
            conversations['messages'][i] = process_img_message_content(message)
        
        return conversations, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
def process_img_message_content(message):
    import base64
    if 'content' in message and isinstance(message['content'], list):
        for content_item in message['content']:
            if content_item.get('type') == 'image_path' and 'path' in content_item:
                small_path = content_item['path'].replace('_large.jpg', '_small.jpg')
                
                try:
                    with open(small_path, 'rb') as image_file:
                        image_data = image_file.read()
                        base64_string = base64.b64encode(image_data).decode('utf-8')
                        content_item['path'] = f"data:image/jpeg;base64,{base64_string}"
                except FileNotFoundError:
                    print(f"Image file not found: {small_path}")
                    pass
                except Exception as e:
                    print(f"Error processing image {small_path}: {str(e)}")
                    pass
    
    return message