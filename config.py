import os
basedir = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or '5JjHyEAl0m9xiB5aOc22Uv1vXY3oyoAW'
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY') or 'UtCHKZOZt7UTh9wbFs8IEwhqXOiXX8PE'
    FERNET_KEY = os.environ.get('FERNET_KEY') or 'jE+bVWCJwdV/1wW6bhHC8CTt/I3xnN7pMpHuK1RRoVQ='
    CORS_HEADERS = 'Content-Type'
    JWT_ACCESS_TOKEN_EXPIRES = 18000

class DevelopmentConfig(Config):
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'QgKKZmscNCpwHjCm7DLoPmo8hqcQEAJ0'
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY') or 'Tvud8R2GXBNfzeoXlnbMFBw4AgVFfBMW'
    FERNET_KEY = os.environ.get('FERNET_KEY') or '7GKpCE7QcRVIXwHFQ8FH3ojm4uxp2YXeJSEf9NR6L48='
    CORS_HEADERS = 'Content-Type'
    JWT_ACCESS_TOKEN_EXPIRES = 18000

class ProductionConfig(Config):
    pass


config = {
    'DEV': DevelopmentConfig,
    'PROD': DevelopmentConfig
}