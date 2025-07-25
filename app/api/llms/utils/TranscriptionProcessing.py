from app.utils import DatabaseHandler, CacheHandler
from app.api.llms.models import Conversation, ConversationUpdate
from bson.objectid import ObjectId
import datetime
mongodb = DatabaseHandler.DatabaseHandler()


def create_transcription_conversation(body, provider, user):
    message = body['message']
    model = body['model']['id']
    record_id = body['id']
    processing_slug = body['slug']
    conversation_id = body['conversation_id']
    
    from app.api.records.services import get_by_id
    resp_, status = get_by_id(record_id, user)
    if status != 200:
        raise Exception('Error al obtener el record')
    
    try:
        from app.utils.functions import cache_get_record_transcription
        processing = cache_get_record_transcription(record_id, processing_slug, False)
    except Exception as e:
        raise Exception('Error al obtener el procesamiento del record')
    
    transcription_text = str(processing['text'])
    tokens = provider.calculate_tokens(transcription_text)
    print(f"Tokens: {tokens}")
    
    from . import prompts
    
    messages = [
        {
            'role': 'system',
            'content': prompts.transcription_basic_asist_system_prompt
        },
        {
            'role': 'user',
            'content': "Transcription:\n\n" + processing['text']
        }
    ]
    
    if conversation_id:
        conversation = mongodb.get_record('conversations', {'_id': ObjectId(conversation_id)}, fields={'messages': 1})
        
        for msg in conversation['messages']:
            messages.append({
                'role': msg['role'],
                'content': msg['content']
            })
    
    messages.append({
        'role': 'user',
        'content': message
    })
    
    resp = provider.call(messages, model=model)
    
    if conversation_id:
        messages = conversation['messages'] + [
            {
                'role': 'user',
                'content': message
            },
            {
                'role': 'assistant',
                'content': resp['choices'][0]['message']['content']
            }
        ]
        
        payload = ConversationUpdate(
            messages=messages,
            updated_at=datetime.datetime.now()
        )
        
        mongodb.update_record('conversations', {'_id': ObjectId(conversation_id)}, payload)
        return {
            'response': resp['choices'][0]['message']['content'],
            'conversation_id': conversation_id
        }
    else:
        payload = {
            'user': user,
            'messages': [
                {
                    'role': 'user',
                    'content': message
                },
                {
                    'role': 'assistant',
                    'content': resp['choices'][0]['message']['content']
                }
            ],
            'type': 'transcription',
            'processing_slug': processing_slug,
            'record_id': record_id,
            'created_at': datetime.datetime.now(),
            'updated_at': datetime.datetime.now()
        }
        
        payload = Conversation(**payload)
        inserted_doc = mongodb.insert_record('conversations', payload)
        
        return {
            'response': resp['choices'][0]['message']['content'],
            'conversation_id': str(inserted_doc.inserted_id)
        }