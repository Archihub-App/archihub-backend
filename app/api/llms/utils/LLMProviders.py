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
                "description": "A variant of GPT-3.5 optimized for chat.",
                "type": "chat"
            },
            {
                "id": "gpt-4",
                "name": "GPT-4",
                "description": "The latest and most powerful model from OpenAI.",
                "type": "chat"
            }
        ]
        
    def call(self, prompt, **kwargs):
        url = 'https://api.openai.com/v1/chat/completions'
        headers = {"Authorization": f"Bearer {self.key}", 'Content-Type': 'application/json'}
        
        data = {
            "model": kwargs.get("model", "gpt-3.5-turbo"),
            "input": [
                {"role": "user", "content": [
                    {
                        "type": "input_text",
                        "text": prompt
                    }
                ]}
            ],
            "max_output_tokens": kwargs.get("max_output_tokens", 2048),
            "temperature": kwargs.get("temperature", 0.7),
        }
        response = requests.post(url, headers=headers, json=data)
        return response.json()