from flask import Flask
from flasgger import Swagger
from config import Config
from flask_jwt_extended import JWTManager


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Inicializar JWT
    jwt = JWTManager(app)
    # Inicializar Swagger
    swagger = Swagger(app)

    # Registrar users blueprint
    from app.api.users import bp as users_bp
    app.register_blueprint(users_bp, url_prefix='/users')

    # Registrar auth blueprint
    from app.api.auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')
    return app