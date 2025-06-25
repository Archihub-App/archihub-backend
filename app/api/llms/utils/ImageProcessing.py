from app.utils import DatabaseHandler, CacheHandler
from app.api.llms.models import Conversation, ConversationUpdate
from bson.objectid import ObjectId
import datetime
from dotenv import load_dotenv
import os

load_dotenv()
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
    
    should_include_image = True
    
    if conversation_id:
        conversation = mongodb.get_record('conversations', {'_id': ObjectId(conversation_id)}, fields={'messages': 1})
        
        if (conversation.get('resource_id') == resource_id and 
            conversation.get('page') == page and 
            len(conversation['messages']) > 0):
            
            # Find the last user message
            last_user_message = None
            for msg in reversed(conversation['messages']):
                if msg['role'] == 'user':
                    last_user_message = msg
                    break
            
            # Check if the last user message contained an image
            if (last_user_message and 
                isinstance(last_user_message.get('content'), list) and 
                any(item.get('type') == 'image_path' for item in last_user_message['content'])):
                should_include_image = False
                
        for msg in conversation['messages']:
            messages.append({
                'role': msg['role'],
                'content': msg['content']
            })
            
    user_message_content = []
    if should_include_image:
        user_message_content.append({
            'type': 'image_path',
            'path': image_path
        })
    
    user_message_content.append({
        'type': 'text',
        'text': message
    })
    
    messages.append({
        'role': 'user',
        'content': user_message_content
    })
    
    # Call the LLM provider
    resp = provider.call(messages, model=model)
    
    storage_user_message = {
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
    }
    
    assistant_message = {
        'role': 'assistant',
        'content': resp['choices'][0]['message']['content']
    }
    
    # Handle conversation update or creation
    if conversation_id:
        # Update existing conversation
        updated_messages = conversation['messages'] + [storage_user_message, assistant_message]
        
        payload = ConversationUpdate(
            messages=updated_messages,
            resource_id=resource_id,
            page=page,
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
            'messages': [storage_user_message, assistant_message],
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