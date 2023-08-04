from flask import Flask
from flasgger import Swagger
from config import config
from flask_jwt_extended import JWTManager
from flask_cors import CORS

def create_app(config_class=config['development']):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # agregar CORS
    CORS(app)
    # agregar headers para que funcione el cors
    app.config['CORS_HEADERS'] = 'Content-Type'

    # Inicializar JWT
    jwt = JWTManager(app)
    # modificar la duración del token a 5 horas
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = 18000
    # Inicializar Swagger
    swagger = Swagger(app)
    # agregar security definition para JWT Bearer type para que aparezca en la documentación la opción de agregar el token
    swagger.config['securityDefinitions'] = {
        'JWT': {
            'type': 'apiKey',
            'name': 'Authorization',
            'in': 'header'
        }
    }

    # Registrar users blueprint
    from app.api.users import bp as users_bp
    app.register_blueprint(users_bp, url_prefix='/users')

    # Registrar auth blueprint
    from app.api.auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')
    
    # Registrar types blueprint
    from app.api.types import bp as types_bp
    app.register_blueprint(types_bp, url_prefix='/types')

    # Registrar logs blueprint
    from app.api.logs import bp as logs_bp
    app.register_blueprint(logs_bp, url_prefix='/logs')

    # Registrar forms blueprint
    from app.api.forms import bp as forms_bp
    app.register_blueprint(forms_bp, url_prefix='/forms')

    return app

    