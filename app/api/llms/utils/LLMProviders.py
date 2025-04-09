import requests
from .BaseLLMProvider import BaseLLMProvider

llm_providers = [
    "OpenAI"
]

def get_llm_providers():
    return llm_providers

class OpenAIProvider(BaseLLMProvider):
    def getModels(self):
        return [
            {
                "id": "gpt-3.5-turbo",
                "name": "GPT-3.5 Turbo",
                "type": "chat"
            },
            {
                "id": "gpt-3.5-turbo-16k",
                "name": "GPT-3.5 Turbo 16k",
            },
            {
                "id": "gpt-4",
                "name": "GPT-4",
                "type": "chat"
            },
            {
                "id": "gpt-4-turbo",
                "name": "GPT-4 Turbo",
                "type": "chat"
            },
            {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "type": "chat"
            },
            {
                "id": "gpt-4o-mini",
                "name": "GPT-4o Mini",
                "type": "chat"
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