from app.utils import DatabaseHandler, CacheHandler
from app.api.aiservices.models import Conversation, ConversationUpdate
from bson.objectid import ObjectId
import datetime
mongodb = DatabaseHandler.DatabaseHandler()

def order_and_filter_blocks(page_data):
    """
    Orders blocks from top to bottom and removes smaller blocks that overlap
    with others by more than 35% of the average area.
    
    Args:
        page_data (dict): Dictionary containing 'blocks' key with a list of block objects
        
    Returns:
        dict: Data dictionary with sorted and filtered blocks
    """
    # Extract blocks
    blocks = page_data['blocks']
    
    # Sort blocks by y-coordinate (top to bottom)
    sorted_blocks = sorted(blocks, key=lambda block: block['bbox']['y'])
    
    # Calculate average area of all blocks
    areas = []
    for block in sorted_blocks:
        bbox = block['bbox']
        area = bbox['width'] * bbox['height']
        areas.append(area)
    average_area = sum(areas) / len(areas) if areas else 0
    
    # Use a set to track indices to remove
    to_remove = set()
    
    # Check each pair of blocks for significant overlap
    for i in range(len(sorted_blocks)):
        if i in to_remove:
            continue
            
        for j in range(i+1, len(sorted_blocks)):
            if j in to_remove:
                continue
                
            bbox1 = sorted_blocks[i]['bbox']
            bbox2 = sorted_blocks[j]['bbox']
            
            # Calculate overlap
            x_overlap = max(0, min(bbox1['x'] + bbox1['width'], bbox2['x'] + bbox2['width']) - max(bbox1['x'], bbox2['x']))
            y_overlap = max(0, min(bbox1['y'] + bbox1['height'], bbox2['y'] + bbox2['height']) - max(bbox1['y'], bbox2['y']))
            overlap_area = x_overlap * y_overlap
            
            # Check if overlap is more than 35% of average area
            if overlap_area > 0.35 * average_area:
                # Calculate areas of both blocks
                area1 = bbox1['width'] * bbox1['height']
                area2 = bbox2['width'] * bbox2['height']
                
                # Mark the smaller block for removal
                if area1 < area2:
                    to_remove.add(i)
                    break  # No need to check this block further
                else:
                    to_remove.add(j)
    
    # Create filtered list keeping only blocks not marked for removal
    filtered_blocks = [block for i, block in enumerate(sorted_blocks) if i not in to_remove]
    
    # Create new data dictionary with filtered blocks
    result = page_data.copy()
    result['blocks'] = filtered_blocks
    return result

def extract_clean_text(ordered_data):
    """
    Takes ordered and filtered blocks and extracts clean text for LLM processing.
    
    Args:
        ordered_data (dict): Dictionary containing ordered and filtered blocks
        
    Returns:
        str: Clean text extracted from blocks, preserving structure
    """
    blocks = ordered_data.get('blocks', [])
    text_content = []
    
    for block in blocks:
        # Skip blocks without text
        if 'text' not in block or not block['text'].strip():
            continue
        
        block_text = block['text'].strip()
        block_type = block.get('type', 'Text')
        
        # Format based on block type
        if block_type == 'Title':
            # Add extra emphasis for titles
            text_content.append(f"# {block_text}")
        else:
            text_content.append(block_text)
    
    # Join blocks with double newlines to preserve paragraph structure
    clean_text = "\n\n".join(text_content)
    
    return clean_text

def create_document_conversation(body, provider, user):
    message = body['message']
    model = body['model']['id']
    record_id = body['id']
    processing_slug = body['slug']
    conversation_id = body['conversation_id']
    opts = body.get('opts', {})
    page = opts.get('page', 1)
    
    from app.api.records.services import get_by_id
    resp_, status = get_by_id(record_id, user)
    if status != 200:
        raise Exception('Error al obtener el record')
    
    from app.utils.functions import cache_get_block_by_page_id
    try:
        processing, status = cache_get_block_by_page_id(record_id, page, processing_slug, 'blocks')
    except Exception as e:
        raise Exception('Error al obtener el procesamiento del record')
    
    ordered_data = order_and_filter_blocks(processing)
    
    clean_text = extract_clean_text(ordered_data)
    
    clean_text = """
    ---
    PAGE: {page}
    ---
    """.format(page=page) + clean_text
    
    tokens = provider.calculate_tokens(clean_text)
    print(f"Tokens: {tokens}")
    
    from . import prompts
    
    messages = [
        {
            'role': 'system',
            'content': prompts.document_basic_asist_system_prompt
        }
    ]
    
    if conversation_id:
        conversation = mongodb.get_record('conversations', {'_id': ObjectId(conversation_id)}, fields={'messages': 1})
        
        for msg in conversation['messages']:
            messages.append({
                'role': msg['role'],
                'content': msg['content']
            })
            
    messages.append(
        {
            'role': 'user',
            'content': "Document content:\n\n" + clean_text
        }
    )
    
    messages.append(
        {
            'role': 'user',
            'content': message
        }
    )
    
    resp = provider.call(messages, model=model)
    
    if conversation_id:
        messages = conversation['messages'] + [
            {
                'role': 'user',
                'content': "Document content:\n\n" + clean_text
            },{
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
                    'content': "Document content:\n\n" + clean_text
                },
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