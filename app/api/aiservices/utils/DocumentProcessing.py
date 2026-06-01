from app.utils import DatabaseHandler
from app.api.aiservices.models import Conversation, ConversationUpdate
from bson.objectid import ObjectId
import datetime
import os
from flask import Response, stream_with_context
from .StreamingUtils import (
    ThinkingStepTracker,
    extract_stream_chunk_parts,
    resolve_stream_flag,
    sse_data,
)
mongodb = DatabaseHandler.DatabaseHandler()
WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')


def get_document_page_image_path(record_id, page):
    record = mongodb.get_record('records', {'_id': ObjectId(record_id)}, fields={'processing.fileProcessing': 1})

    if not record:
        raise Exception('Record no existe')

    if 'processing' not in record or 'fileProcessing' not in record['processing']:
        raise Exception('Record no ha sido procesado')

    file_processing = record['processing']['fileProcessing']
    if file_processing.get('type') != 'document':
        raise Exception('Record no es de tipo document')

    path = file_processing.get('path')
    if not path:
        raise Exception('Record no ha sido procesado')

    path_files = os.path.join(WEB_FILES_PATH, path, 'web/big/')
    if not os.path.exists(path_files):
        raise Exception('No existe la ruta del documento')

    files = sorted(os.listdir(path_files))
    if page < 1 or page > len(files):
        raise Exception('Record no tiene tantas páginas')

    image_path = os.path.join(path_files, files[page - 1])
    if not os.path.exists(image_path):
        raise Exception('No existe el archivo')

    return image_path

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
    applied_skills = body.get('applied_skills', [])
    skill_paths = body.get('skill_paths', [])
    opts = body.get('opts', {})
    opt = body.get('opt', 'document_ocr')
    try:
        page = int(opts.get('page', 1))
    except (TypeError, ValueError):
        page = 1

    if opt not in ('document_ocr', 'image'):
        opt = 'document_ocr'

    stream = resolve_stream_flag(body)
    
    from app.api.records.services import get_by_id
    resp_, status = get_by_id(record_id, user)
    if status != 200:
        raise Exception('Error al obtener el record')
    
    page_image_path = None
    clean_text = None

    if opt == 'image':
        page_image_path = get_document_page_image_path(record_id, page)
    else:
        from app.utils.functions import cache_get_block_by_page_id
        try:
            processing, status = cache_get_block_by_page_id(record_id, page, processing_slug, 'blocks')
        except Exception:
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
    
    conversation = None
    if conversation_id:
        conversation = mongodb.get_record('conversations', {'_id': ObjectId(conversation_id)}, fields={'messages': 1})
        
        for msg in conversation['messages']:
            messages.append({
                'role': msg['role'],
                'content': msg['content']
            })
            
    if opt == 'image':
        user_turn = {
            'role': 'user',
            'content': [
                {
                    'type': 'image_path',
                    'path': page_image_path
                },
                {
                    'type': 'text',
                    'text': f"Document page: {page}\n\n{message}"
                }
            ]
        }
    else:
        # Combine document context and user question into a single user turn so that
        # providers which reject consecutive same-role messages work correctly.
        user_turn = {
            'role': 'user',
            'content': f"Document content:\n\n{clean_text}\n\n---\n\n{message}"
        }

    messages.append(user_turn)

    resp = provider.call(
        messages,
        model=model,
        stream=stream,
        skill_paths=skill_paths,
        skills=applied_skills,
        skill_context_applied=False,
    )

    if stream and not isinstance(resp, dict):
        def generate():
            response_parts = []
            thinking_tracker = ThinkingStepTracker()
            try:
                for chunk in resp:
                    chunk_parts = extract_stream_chunk_parts(chunk)

                    thinking_delta = chunk_parts.get('thinking', '')
                    if thinking_delta:
                        for step_event in thinking_tracker.consume_thinking(thinking_delta):
                            yield sse_data(step_event)

                    response_delta = chunk_parts.get('response', '')
                    if response_delta:
                        response_parts.append(response_delta)
                        yield sse_data({'type': 'response', 'delta': response_delta})

                full_response = ''.join(response_parts)
                assistant_turn = {'role': 'assistant', 'content': full_response}

                if conversation_id:
                    updated_messages = conversation['messages'] + [user_turn, assistant_turn]

                    payload = ConversationUpdate(
                        messages=updated_messages,
                        applied_skills=applied_skills,
                        updated_at=datetime.datetime.now()
                    )

                    mongodb.update_record('conversations', {'_id': ObjectId(conversation_id)}, payload)
                    final_conversation_id = conversation_id
                else:
                    payload = {
                        'user': user,
                        'messages': [user_turn, assistant_turn],
                        'type': 'document',
                        'processing_slug': processing_slug,
                        'record_id': record_id,
                        'applied_skills': applied_skills,
                        'created_at': datetime.datetime.now(),
                        'updated_at': datetime.datetime.now()
                    }

                    payload = Conversation(**payload)
                    inserted_doc = mongodb.insert_record('conversations', payload)
                    final_conversation_id = str(inserted_doc.inserted_id)

                for step_event in thinking_tracker.finalize():
                    yield sse_data(step_event)

                yield sse_data({
                    'type': 'done',
                    'done': True,
                    'conversation_id': final_conversation_id,
                    'thinking_steps': thinking_tracker.summary(),
                })
            except Exception as e:
                yield sse_data({'type': 'error', 'error': str(e), 'done': True})

        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
            }
        )

    assistant_turn = {'role': 'assistant', 'content': resp['choices'][0]['message']['content']}

    if conversation_id:
        messages = conversation['messages'] + [user_turn, assistant_turn]
        
        payload = ConversationUpdate(
            messages=messages,
            applied_skills=applied_skills,
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
            'messages': [user_turn, assistant_turn],
            'type': 'document',
            'processing_slug': processing_slug,
            'record_id': record_id,
            'applied_skills': applied_skills,
            'created_at': datetime.datetime.now(),
            'updated_at': datetime.datetime.now()
        }
        
        payload = Conversation(**payload)
        inserted_doc = mongodb.insert_record('conversations', payload)
        
        return {
            'response': resp['choices'][0]['message']['content'],
            'conversation_id': str(inserted_doc.inserted_id)
        }