import requests
from .BaseLLMProvider import BaseLLMProvider

llm_providers = [
    "OpenAI",
    "Google"
]

def get_llm_providers():
    return llm_providers

class GoogleProvider(BaseLLMProvider):
    def getModels(self):
        return [
            {
                "id": "gemini-2.0-flash",
                "name": "Gemini 2.0 Flash",
                "type": "chat",
                "max_tokens": 2048,
            },
            {
                "id": "gemini-2.0-flash-lite",
                "name": "Gemini 2.0 Flash Lite",
                "type": "chat"
            },
            {
                "id": "gemini-2.0-flash-thinking-exp-01-21",
                "name": "Gemini 2.0 Flash Thinking Exp 01-21",
                "type": "chat"
            },
            {
                "id": "gemini-2.5-pro-preview-03-25",
                "name": "Gemini 2.5 Pro Preview 03-25",
                "type": "chat"
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
        
        contents =[
            types.Content(
                role=msg['role'] if msg['role'] in ['user', 'assistant'] else 'user',
                parts=[
                    types.Part.from_text(text=msg['content'])
                ]
            ) for msg in messages
        ]
        
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

class OpenAIProvider(BaseLLMProvider):
    def getModels(self):
        return [
            {
                "id": "gpt-3.5-turbo",
                "name": "GPT-3.5 Turbo",
                "type": "chat",
                "max_tokens": 16384,
            },
            {
                "id": "gpt-4",
                "name": "GPT-4",
                "type": "chat",
                "max_tokens": 32768,
            },
            {
                "id": "gpt-4-turbo",
                "name": "GPT-4 Turbo",
                "type": "chat",
                "max_tokens": 100000,
            },
            {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "type": "chat",
                "max_tokens": 100000,
            },
            {
                "id": "gpt-4o-mini",
                "name": "GPT-4o Mini",
                "type": "chat",
                "max_tokens": 100000,
            }
        ]
        
    def call(self, messages, **kwargs):
        url = 'https://api.openai.com/v1/chat/completions'
        headers = {"Authorization": f"Bearer {self.key}", 'Content-Type': 'application/json'}
        
        # check if the model is in the list of models
        models = self.getModels()
        model_ids = [model['id'] for model in models]
        if kwargs.get("model") not in model_ids:
            raise ValueError(f"Model {kwargs.get('model')} is not supported. Supported models are: {model_ids}")
        
        data = {
            "model": kwargs.get("model", "gpt-3.5-turbo"),
            "messages": messages,
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