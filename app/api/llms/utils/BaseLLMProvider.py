from flask_babel import _
from cryptography.fernet import Fernet
from config import config
import os

fernet_key = config[os.environ['FLASK_ENV']].FERNET_KEY
fernet = Fernet(fernet_key)

class BaseLLMProvider:
    def __init__(self, name, key):
        self.name = name
        self.key = fernet.decrypt(key.encode()).decode()

    def call(self, prompt, **kwargs):
        raise NotImplementedError(_("This method should be overridden by subclasses."))