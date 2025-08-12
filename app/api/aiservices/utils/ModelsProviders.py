import requests
from .BaseLLMProvider import BaseLLMProvider
import mimetypes
import base64
import os

llm_providers = [
    "OpenAI",
    "Google",
    "Azure",
    "Ollama"
]

def get_llm_providers():
    return llm_providers


class AzureProvider(BaseLLMProvider):
    def getModels(self):
        return [
            {
                "id": "gpt-4.1",
                "name": "GPT-4.1",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
                "cognitive_services": True,
            },
            {
                "id": "gpt-4.1-mini",
                "name": "GPT-4.1 Mini",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
                "cognitive_services": True,
            },
            {
                "id": "gpt-4.1-nano",
                "name": "GPT-4.1 Nano",
                "type": "chat",
                "max_tokens": 16384,
                "capabilities": ["chat", "image"],
                "cognitive_services": True,
            },
            {
                "id": "o4-mini",
                "name": "o4 Mini",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
                "cognitive_services": True,
            },
            {
                "id": "gpt-35-turbo",
                "name": "GPT-3.5 Turbo",
                "type": "chat",
                "max_tokens": 16384,
                "capabilities": ["chat"],
                "cognitive_services": True,
            },
            {
                "id": "grok-3",
                "name": "Grok 3",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "grok-3-mini",
                "name": "Grok 3 Mini",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "DeepSeek-R1-0528",
                "name": "DeepSeek R1 0528",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            }
        ]
        
    def call(self, messages, **kwargs):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.key}",
        }
        
        model = kwargs.get("model", "gpt-4.1")
        models = self.getModels()
        model_ids = [model['id'] for model in models]
        if model not in model_ids:
            raise ValueError(f"Model {model} is not supported. Supported models are: {model_ids}")
        
        processed_messages = []
        for msg in messages:
            if isinstance(msg['content'], str):
                # Simple text message
                processed_messages.append(msg)
            elif isinstance(msg['content'], list):
                # Mixed content (text + images)
                content_parts = []
                for content_item in msg['content']:
                    if content_item['type'] == 'text':
                        content_parts.append({
                            "type": "text",
                            "text": content_item['text']
                        })
                    elif content_item['type'] == 'image_path':
                        # Convert local image to base64 for Azure
                        image_path = content_item['path']
                        base64_image = self.process_image(image_path)
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {
                                "url": base64_image
                            }
                        })
                
                processed_messages.append({
                    "role": msg['role'],
                    "content": content_parts
                })
            else:
                processed_messages.append(msg)
                
        data = {
            "model": model,
            "messages": processed_messages,
            "max_tokens": kwargs.get("max_tokens", 2048),
            "temperature": kwargs.get("temperature", 0.5),
        }
        
        try:
            # find the model in the list of models
            model_info = next((m for m in self.getModels() if m['id'] == model), None)
            url = self.endpoint if not model_info.get("cognitive_services") else self.endpointCognitive
            
            if not url:
                raise ValueError("Endpoint URL is not set for Azure provider.")
            
            response = requests.post(url, headers=headers, json=data)
            response_data = response.json()
            
            if response.status_code != 200:
                raise ValueError(f"Azure API returned an error: {response.status_code} - {response.text}")
            
            if response_data.get("error"):
                raise ValueError(f"Azure API returned an error: {response_data['error']['message']}")
            
            return response_data
        except requests.exceptions.RequestException as e:
            raise ValueError(f"Request to Azure API failed: {e}")
        
        
    def process_image(image_path):
        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')
        
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type or not mime_type.startswith('image/'):
            mime_type = 'image/jpeg'
            
        return f"data:{mime_type};base64,{base64_image}"

class GoogleProvider(BaseLLMProvider):
    def getModels(self):
        return [
            {
                "id": "gemini-2.0-flash",
                "name": "Gemini 2.0 Flash",
                "type": "chat",
                "max_tokens": 2048,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gemini-2.0-flash-lite",
                "name": "Gemini 2.0 Flash Lite",
                "type": "chat",
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gemini-2.0-flash-thinking-exp-01-21",
                "name": "Gemini 2.0 Flash Thinking Exp 01-21",
                "type": "chat",
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gemini-2.5-pro-preview-03-25",
                "name": "Gemini 2.5 Pro Preview 03-25",
                "type": "chat",
                "capabilities": ["chat", "image"],
            }
        ]
        
    def call(self, messages, **kwargs):
        from google import genai
        from google.genai import types
        
        client = genai.Client(
            api_key=self.key,
        )
        
        models = self.getModels()
        model_ids = [model['id'] for model in models]
        if kwargs.get("model") not in model_ids:
            raise ValueError(f"Model {kwargs.get('model')} is not supported. Supported models are: {model_ids}")
        
        contents = []
        for msg in messages:
            role = msg['role'] if msg['role'] in ['user', 'assistant'] else 'user'
            parts = []
            
            if isinstance(msg['content'], str):
                parts.append(types.Part.from_text(text=msg['content']))
            elif isinstance(msg['content'], list):
                for content_item in msg['content']:
                    if content_item['type'] == 'text':
                        parts.append(types.Part.from_text(text=content_item['text']))
                    elif content_item['type'] == 'image_path':
                        image_part = self.process_image(content_item['path'])
                        if image_part:
                            parts.append(image_part)
            
            contents.append(types.Content(role=role, parts=parts))
        
        generate_content_config = types.GenerateContentConfig(
            temperature=kwargs.get("temperature", 0.5),
            max_output_tokens=kwargs.get("max_tokens", 2048),
            response_mime_type="text/plain",
        )
        
        response = ""
        for chunk in client.models.generate_content_stream(
            model=kwargs.get("model", "gemini-2.0-flash"),
            contents=contents,
            config=generate_content_config,
        ):
            response += chunk.text
        
        return {
            'choices': [
                {
                    'message': {
                        'role': 'assistant',
                        'content': response
                    }
                }
            ],
        }
        
    def process_image(self, image_path):
        from google.genai import types
        
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type or not mime_type.startswith('image/'):
            mime_type = 'image/jpeg'
        
        with open(image_path, 'rb') as f:
            image_bytes = f.read()
            
        return types.Part.from_bytes(
            data=image_bytes,
            mime_type=mime_type
        )

class OpenAIProvider(BaseLLMProvider):
    def getModels(self):
        return [
            {
                "id": "gpt-4.1-nano",
                "name": "GPT-4.1 Nano",
                "type": "chat",
                "max_tokens": 16384,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gpt-4.1-mini",
                "name": "GPT-4.1 Mini",
                "type": "chat",
                "max_tokens": 16384,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gpt-4.1",
                "name": "GPT-4.1",
                "type": "chat",
                "max_tokens": 16384,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gpt-3.5-turbo",
                "name": "GPT-3.5 Turbo",
                "type": "chat",
                "capabilities": ["chat"],
                "max_tokens": 16384,
            },
            {
                "id": "gpt-4",
                "name": "GPT-4",
                "type": "chat",
                "max_tokens": 32768,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gpt-4-turbo",
                "name": "GPT-4 Turbo",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gpt-4o-mini",
                "name": "GPT-4o Mini",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "o1",
                "name": "o1",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "o4-mini",
                "name": "o4 Mini",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            }
        ]
        
    def call(self, messages, **kwargs):
        url = 'https://api.openai.com/v1/chat/completions'
        headers = {"Authorization": f"Bearer {self.key}", 'Content-Type': 'application/json'}
        model = kwargs.get("model", "gpt-3.5-turbo")
        
        # check if the model is in the list of models
        models = self.getModels()
        model_ids = [model['id'] for model in models]
        if model not in model_ids:
            raise ValueError(f"Model {model} is not supported. Supported models are: {model_ids}")
        
        processed_messages = []
        
        for msg in messages:
            if isinstance(msg['content'], str):
                # Simple text message
                processed_messages.append(msg)
            elif isinstance(msg['content'], list):
                # Mixed content (text + images)
                content_parts = []
                for content_item in msg['content']:
                    if content_item['type'] == 'text':
                        content_parts.append({
                            "type": "text",
                            "text": content_item['text']
                        })
                    elif content_item['type'] == 'image_path':
                        # Convert local image to base64 for OpenAI
                        image_path = content_item['path']
                        base64_image = self.process_image(image_path)
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {
                                "url": base64_image
                            }
                        })
                
                processed_messages.append({
                    "role": msg['role'],
                    "content": content_parts
                })
            else:
                processed_messages.append(msg)
        
        data = {
            "model": model,
            "messages": processed_messages,
            "max_tokens": kwargs.get("max_tokens", 2048),
            "temperature": kwargs.get("temperature", 0.5),
        }
        
        try:
            response = requests.post(url, headers=headers, json=data)
            
            response_data = response.json()
            if response.status_code != 200:
                raise ValueError(f"OpenAI API returned an error: {response.status_code} - {response.text}")
            
            if response_data.get("error"):
                raise ValueError(f"OpenAI API returned an error: {response_data['error']['message']}")
            
            return response_data
        except requests.exceptions.RequestException as e:
            raise ValueError(f"Request to OpenAI API failed: {e}")
        
    def process_image(self, image_path):
        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')
            
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type or not mime_type.startswith('image/'):
            mime_type = 'image/jpeg'
            
        return f"data:{mime_type};base64,{base64_image}"

class OllamaProvider(BaseLLMProvider):
    def getModels(self):
        return [
            {
                "id": "gpt-oss:20b",
                "name": "GPT-OSS 20b",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat"],
            },
            {
                "id": "gemma3:4b",
                "name": "Gemma 3 4b",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            }
        ]
    
    def call(self, messages, **kwargs):
        url = f"{os.getenv('OLLAMA_HOST')}:{os.getenv('OLLAMA_PORT')}/api/chat"
        model = kwargs.get("model", "gpt-oss:20b")

        # check if the model is in the list of models
        models = self.getModels()
        model_ids = [model['id'] for model in models]
        if model not in model_ids:
            raise ValueError(f"Model {model} is not supported. Supported models are: {model_ids}")

        processed_messages = []

        for msg in messages:
            if isinstance(msg['content'], str):
                # Simple text message
                processed_messages.append({
                    "role": msg['role'],
                    "content": msg['content']
                })
            elif isinstance(msg['content'], list):
                # Mixed content (text + images) — Ollama expects string content + images array
                text_chunks = []
                image_parts = []
                for content_item in msg['content']:
                    if content_item['type'] == 'text':
                        text_chunks.append(content_item['text'])
                    elif content_item['type'] == 'image_path':
                        # Convert local image to base64 for Ollama
                        image_path = content_item['path']
                        base64_image = self.process_image(image_path)
                        image_parts.append(base64_image)

                merged_text = "\n".join(text_chunks) if text_chunks else ""
                message_dict = {
                    "role": msg['role'],
                    "content": merged_text
                }
                if image_parts:
                    message_dict["images"] = image_parts

                processed_messages.append(message_dict)
            else:
                # Fallback: pass through as-is if unexpected structure
                processed_messages.append(msg)

        data = {
            "model": model,
            "messages": processed_messages,
            "stream": False
        }
        
        try:
            response = requests.post(url, json=data)

            response_data = response.json()
            if response.status_code != 200:
                raise ValueError(f"Ollama returned an error: {response.status_code} - {response.text}")

            if response_data.get("error"):
                raise ValueError(f"Ollama returned an error: {response_data['error']['message']}")

            return {
                'choices': [
                    {
                        'message': response_data['message']
                    }
                ]
            }
        except requests.exceptions.RequestException as e:
            raise ValueError(f"Request to Ollama API failed: {e}")

    def process_image(self, image_path):
        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')
        return base64_image