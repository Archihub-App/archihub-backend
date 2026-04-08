from flask_babel import _
from cryptography.fernet import Fernet
from config import config
import os
import tiktoken

fernet_key = config[os.environ['FLASK_ENV']].FERNET_KEY
fernet = Fernet(fernet_key)

class BaseLLMProvider:
    def __init__(self, name, key, **kwargs):
        self.name = name
        self.key = fernet.decrypt(key.encode()).decode()
        self.endpoint = kwargs.get('endpoint', None)
        self.endpointCognitive = kwargs.get('endpointCognitive', None)
        
    def call(self, prompt, **kwargs):
        raise NotImplementedError(_("This method should be overridden by subclasses."))
    
    def process_image(self, image):
        raise NotImplementedError(_("This method should be overridden by subclasses."))
    
    def calculate_tokens(self, text, model_name_or_path="gpt-3.5-turbo"):
        if not isinstance(text, str):
            print(f"Warning: Input to calculate_tokens is not a string (type: {type(text)}). Returning 0 tokens.")
            return 0

        try:
            enc = tiktoken.encoding_for_model(model_name_or_path)
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")

        return len(enc.encode(text))