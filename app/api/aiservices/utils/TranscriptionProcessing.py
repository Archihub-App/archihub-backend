from app.utils import DatabaseHandler
from app.api.aiservices.models import Conversation, ConversationUpdate
from bson.objectid import ObjectId
import datetime
from flask import Response, stream_with_context
from .StreamingUtils import (
    ThinkingStepTracker,
    extract_stream_chunk_parts,
    resolve_stream_flag,
    sse_data,
)
mongodb = DatabaseHandler.DatabaseHandler()


def create_transcription_conversation(body, provider, user):
    message = body['message']
    model = body['model']['id']
    record_id = body['id']
    processing_slug = body['slug']
    conversation_id = body['conversation_id']
    stream = resolve_stream_flag(body)
    
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

    # Build the message list:
    #  1. System prompt
    #  2. Transcription as a user context message (so providers that reject
    #     mid-conversation system messages work correctly)
    #  3. Conversation history (if resuming)
    #  4. New user question
    messages = [
        {
            'role': 'system',
            'content': prompts.transcription_basic_asist_system_prompt
        },
        {
            'role': 'user',
            'content': "Transcription:\n\n" + processing['text']
        },
        {
            'role': 'assistant',
            'content': 'I have read the transcription. How can I help?'
        }
    ]

    conversation = None
    if conversation_id:
        conversation = mongodb.get_record('conversations', {'_id': ObjectId(conversation_id)}, fields={'messages': 1})
        for msg in conversation['messages']:
            messages.append({'role': msg['role'], 'content': msg['content']})

    messages.append({'role': 'user', 'content': message})

    resp = provider.call(messages, model=model, stream=stream)

    if stream and not isinstance(resp, dict):
        user_turn = {
            'role': 'user',
            'content': message
        }

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
                assistant_turn = {
                    'role': 'assistant',
                    'content': full_response
                }

                if conversation_id:
                    updated_messages = conversation['messages'] + [user_turn, assistant_turn]
                    payload = ConversationUpdate(
                        messages=updated_messages,
                        updated_at=datetime.datetime.now()
                    )
                    mongodb.update_record('conversations', {'_id': ObjectId(conversation_id)}, payload)
                    final_conversation_id = conversation_id
                else:
                    payload = {
                        'user': user,
                        'messages': [user_turn, assistant_turn],
                        'type': 'transcription',
                        'processing_slug': processing_slug,
                        'record_id': record_id,
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