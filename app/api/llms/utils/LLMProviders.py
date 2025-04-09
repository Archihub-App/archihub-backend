import requests
from .BaseLLMProvider import BaseLLMProvider

llm_providers = [
    "OpenAI"
]

def get_llm_providers():
    return llm_providers

class OpenAIProvider(BaseLLMProvider):
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