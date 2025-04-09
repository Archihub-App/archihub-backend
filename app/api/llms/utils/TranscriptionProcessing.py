from app.utils import DatabaseHandler, CacheHandler
from app.api.llms.models import Conversation, ConversationUpdate
mongodb = DatabaseHandler.DatabaseHandler()

def create_transcription_conversation(body, provider, user):
    message = body['message']
    model = body['model']['id']
    record_id = body['id']
    processing_slug = body['slug']
    
    from app.api.records.services import get_by_id
    resp_, status = get_by_id(record_id, user)
    if status != 200:
        raise Exception('Error al obtener el record')
    
    try:
        from app.utils.functions import cache_get_record_transcription
        processing = cache_get_record_transcription(record_id, processing_slug, False)
    except Exception as e:
        raise Exception('Error al obtener el procesamiento del record')
    
    messages = [
        {
            'role': 'system',
            'content': "Eres un asistente editorial especializado en analizar transcripciones generadas automáticamente por modelos como Whisper. No editas ni corriges el contenido. Tu función es responder preguntas del usuario sobre la transcripción en español, como identificar palabras, frases, posibles errores, temas tratados o aclarar partes confusas, sin modificar el texto original."
        },
        {
            'role': 'user',
            'content': "Transcipción:\n\n" + processing['text']
        }
    ]
    
    messages.append({
        'role': 'user',
        'content': message
    })
    
    
    
    resp = provider.call(messages, model=model)
    
    payload = {
        'user': user,
        'messages': [
            {
                'role': 'user',
                'text': message
            },
            {
                'role': 'assistant',
                'text': resp['choices'][0]['message']['content']
            }
        ],
        'type': 'transcription',
        'processing_slug': processing_slug,
        'record_id': record_id
    }
    
    payload = Conversation(**payload)
    mongodb.insert_record('conversations', payload)
    
    return resp['choices'][0]['message']['content']