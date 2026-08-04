import os
from dotenv import load_dotenv

load_dotenv()

basedir = os.path.abspath(os.path.dirname(__file__))


def _require_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is not set. "
            "Refusing to start with a missing secret. Set it in development/.env "
            "or the environment before starting the app."
        )
    return value


class Config:
    SECRET_KEY = _require_env('SECRET_KEY')
    JWT_SECRET_KEY = _require_env('JWT_SECRET_KEY')
    FERNET_KEY = _require_env('FERNET_KEY')
    CORS_HEADERS = 'Content-Type'
    JWT_ACCESS_TOKEN_EXPIRES = 18000
    TEST_SECRET_HEADER_KEY = os.environ.get('TEST_SECRET_HEADER_KEY')

class DevelopmentConfig(Config):
    pass

class ProductionConfig(Config):
    pass


config = {
    'DEV': DevelopmentConfig,
    'PROD': ProductionConfig
}
