from flask_babel import _
from cryptography.fernet import Fernet
from config import config
import os
from transformers import AutoTokenizer

fernet_key = config[os.environ['FLASK_ENV']].FERNET_KEY
fernet = Fernet(fernet_key)

class BaseLLMProvider:
    def __init__(self, name, key):
        self.name = name
        self.key = fernet.decrypt(key.encode()).decode()

    def call(self, prompt, **kwargs):
        raise NotImplementedError(_("This method should be overridden by subclasses."))
    
    def process_image(self, image):
        raise NotImplementedError(_("This method should be overridden by subclasses."))
    
    def calculate_tokens(self, text, model_name_or_path="gpt-3.5-turbo"):
        if not isinstance(text, str):
            print(f"Warning: Input to calculate_tokens is not a string (type: {type(text)}). Returning 0 tokens.")
            return 0
    
        try:
            tokenizer = AutoTokenizer.from_pretrained(model_name_or_path)
        except Exception:
            print(f"Warning: Could not load tokenizer for '{model_name_or_path}'. Falling back to 'gpt2'.")
            tokenizer = AutoTokenizer.from_pretrained("gpt2")

        try:
            tokens = tokenizer.encode(text)
            return len(tokens)
        except Exception as e:
            print(f"Error encoding text with tokenizer: {e}")
            return None