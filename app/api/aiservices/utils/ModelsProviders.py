import requests
from .BaseLLMProvider import BaseLLMProvider
import mimetypes
import base64
import os
import time

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
                "id": "gemini-2.0-flash-lite",
                "name": "Gemini 2.0 Flash Lite",
                "type": "chat",
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gemini-2.0-flash",
                "name": "Gemini 2.0 Flash",
                "type": "chat",
                "max_tokens": 2048,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gemini-2.5-flash-lite",
                "name": "Gemini 2.5 Flash Lite",
                "type": "chat",
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gemini-2.5-flash",
                "name": "Gemini 2.5 Flash",
                "type": "chat",
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gemini-2.5-pro",
                "name": "Gemini 2.5 Pro",
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
            raise ValueError(f"Model {kwargs.get('model')} is not supported.{model_ids}")
        
        contents = []
        for msg in messages:
            role = 'model' if msg['role'] == 'assistant' else 'user'
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
            if chunk.text:
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
                "id": "gpt-4o",
                "name": "GPT-4o",
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
            },
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
                "id": "gpt-5-nano",
                "name": "GPT-5 Nano",
                "type": "chat",
                "max_tokens": 16384,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gpt-5-mini",
                "name": "GPT-5 Mini",
                "type": "chat",
                "max_tokens": 16384,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "gpt-5",
                "name": "GPT-5",
                "type": "chat",
                "max_tokens": 16384,
                "capabilities": ["chat", "image"],
            },
        ]
        
    def call(self, messages, **kwargs):
        url = 'https://api.openai.com/v1/chat/completions'
        headers = {"Authorization": f"Bearer {self.key}", 'Content-Type': 'application/json'}
        model = kwargs.get("model", "gpt-3.5-turbo")
        new_models = ["gpt-5", "gpt-5-mini", "gpt-5-nano", "o4-mini"]
        
        # check if the model is in the list of models
        models = self.getModels()
        model_ids = [model['id'] for model in models]
        
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
            "messages": processed_messages
        }

        if model in new_models:
            data["max_completion_tokens"] = kwargs.get("max_tokens", 2048)
        else:
            data["max_tokens"] = kwargs.get("max_tokens", 2048)
            data["temperature"] = kwargs.get("temperature", 0.5)

        max_retries = 5
        backoff_factor = 1

        for attempt in range(max_retries):
            try:
                response = requests.post(url, headers=headers, json=data, timeout=30)
                
                if response.status_code == 429:
                    if attempt < max_retries - 1:
                        sleep_time = backoff_factor * (2 ** attempt)
                        # You might want to use a logger here instead of print
                        print(f"Rate limit exceeded. Retrying in {sleep_time} seconds...")
                        time.sleep(sleep_time)
                        continue
                    else:
                        # Raise the error on the last attempt
                        response.raise_for_status()

                response.raise_for_status()  # Raises an HTTPError for other bad responses (4xx or 5xx)
                response_data = response.json()
                
                if response_data.get("error"):
                    raise ValueError(f"OpenAI API returned an error: {response_data['error']['message']}")
                
                return response_data
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise ValueError(f"Request to OpenAI API failed after {max_retries} retries: {e}")
        
        # This part should not be reached if logic is correct, but as a fallback:
        raise ValueError("Failed to get a response from OpenAI API after multiple retries.")
        
        
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
                "id": "gpt-oss:120b",
                "name": "GPT-OSS 120b",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat"],
            },
            {
                "id": "deepseek-r1:8b",
                "name": "DeepSeek R1 8b",
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
            },
            {
                "id": "gemma3:12b",
                "name": "Gemma 3 12b",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "qwen3-vl:4b",
                "name": "Qwen 3 VL 4b",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "qwen3-vl:8b",
                "name": "Qwen 3 VL 8b",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "qwen3-embedding:8b",
                "name": "Qwen 3 Embedding 8b",
                "type": "embedding",
                "max_tokens": 40000,
                "capabilities": ["embedding"],
            },
            {
                "id": "nomic-embed-text-v2-moe",
                "name": "Nomic Embed Text v2 Moe",
                "type": "embedding",
                "max_tokens": 512,
                "capabilities": ["embedding"],
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
        
        model_info = next((m for m in models if m['id'] == model), None)
        max_tokens = model_info.get('max_tokens', 100000) if model_info else 65536

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

        print("Processed Messages for Ollama:", processed_messages)
        print(processed_messages[0])
        data = {
            "model": model,
            "messages": processed_messages,
            "stream": False,
            "options": {
                "num_ctx": max_tokens
            }
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