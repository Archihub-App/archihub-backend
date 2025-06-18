from app.utils import DatabaseHandler, CacheHandler
from app.api.llms.models import Conversation, ConversationUpdate
from bson.objectid import ObjectId
import datetime
mongodb = DatabaseHandler.DatabaseHandler()

def create_image_gallery_conversation(body, provider, user):
    message = body['message']
    model = body['model']['id']
    resource_id = body['id']
    conversation_id = body['conversation_id']
    opts = body.get('opts', {})
    page = opts.get('page', 0)
    
    from app.api.records.services import get_by_index_gallery
    image = get_by_index_gallery({
        'id': resource_id,
        'index': page
    }, user)
    
    print("Image retrieved:", image)