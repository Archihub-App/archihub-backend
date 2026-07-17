import os
from dotenv import load_dotenv

# Load .env here (not just in app/__init__.py) so required secrets below are
# actually populated regardless of import order — app/__init__.py imports
# `config` before it calls load_dotenv() itself, which previously masked
# missing-secret bugs because the old fallback values papered over it.
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
    # Only used when ARCHIHUB_TEST_MODE=true, to protect /health/test-control/*
    # (see app/utils/TestControlAuth.py). Intentionally has NO fallback and is
    # NOT required at startup: leaving it unset just means test-control auth
    # can never succeed (secret_header != None always fails the comparison),
    # which is the safe default for a feature that must stay off outside
    # disposable test instances.
    TEST_SECRET_HEADER_KEY = os.environ.get('TEST_SECRET_HEADER_KEY')

class DevelopmentConfig(Config):
    pass

class ProductionConfig(Config):
    pass


config = {
    'DEV': DevelopmentConfig,
    'PROD': ProductionConfig
}
