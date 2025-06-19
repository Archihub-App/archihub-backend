from app.utils import DatabaseHandler, CacheHandler
from app.api.llms.models import Conversation, ConversationUpdate
from bson.objectid import ObjectId
import datetime
import os
mongodb = DatabaseHandler.DatabaseHandler()

WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')

def create_image_gallery_conversation(body, provider, user):
    message = body['message']
    model = body['model']['id']
    resource_id = body['id']
    conversation_id = body['conversation_id']
    opts = body.get('opts', {})
    page = opts.get('page', 0)
    
    from app.api.records.services import get_by_index_gallery
    image, status = get_by_index_gallery({
        'id': resource_id,
        'index': page
    }, user)
    
    if status != 200:
        raise Exception('Error retrieving image from gallery')
    
    image_path = mongodb.get_record('records', {'_id': ObjectId(str(image['_id']['$oid']))}, fields={'processing.fileProcessing.path': 1})['processing']['fileProcessing']['path']
    
    image_path = os.path.join(WEB_FILES_PATH, image_path)
    image_path = image_path + '_large.jpg'
    
    from . import prompts
    
    messages = [
        {
            'role': 'system',
            'content': prompts.image_gallery_basic_asist_system_prompt
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
        'content': [
            {
                'type': 'image_path',
                'path': image_path
            },
            {
                'type': 'text',
                'text': message
            }
        ]
    })
    
    # Call the LLM provider
    resp = provider.call(messages, model=model)
    
    # Handle conversation update or creation
    if conversation_id:
        # Update existing conversation
        updated_messages = conversation['messages'] + [
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'image_path',
                        'path': image_path
                    },
                    {
                        'type': 'text',
                        'text': message
                    }
                ]
            },
            {
                'role': 'assistant',
                'content': resp['choices'][0]['message']['content']
            }
        ]
        
        payload = ConversationUpdate(
            messages=updated_messages,
            updated_at=datetime.datetime.now()
        )
        
        mongodb.update_record('conversations', {'_id': ObjectId(conversation_id)}, payload)
        return {
            'response': resp['choices'][0]['message']['content'],
            'conversation_id': conversation_id
        }
    else:
        # Create new conversation
        payload = {
            'user': user,
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image_path',
                            'path': image_path
                        },
                        {
                            'type': 'text',
                            'text': message
                        }
                    ]
                },
                {
                    'role': 'assistant',
                    'content': resp['choices'][0]['message']['content']
                }
            ],
            'type': 'image_gallery',
            'resource_id': resource_id,
            'page': page,
            'created_at': datetime.datetime.now(),
            'updated_at': datetime.datetime.now()
        }
        
        payload = Conversation(**payload)
        inserted_doc = mongodb.insert_record('conversations', payload)
        
        return {
            'response': resp['choices'][0]['message']['content'],
            'conversation_id': str(inserted_doc.inserted_id)
        }