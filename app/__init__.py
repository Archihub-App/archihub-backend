from flask import Flask
from flasgger import Swagger
from config import config
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from celery import Celery
from celery import Task
from flask import Flask

def create_app(config_class=config['development']):
    app = Flask(__name__)
    app.config.from_object(config_class)

    app.config.from_mapping(
        CELERY=dict(
            broker_url="redis://localhost",
            result_backend="redis://localhost",
            task_ignore_result=True,
        ),
    )

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

    # Registrar lists blueprint
    from app.api.lists import bp as lists_bp
    app.register_blueprint(lists_bp, url_prefix='/lists')

    # Registrar resources blueprint
    from app.api.resources import bp as resources_bp
    app.register_blueprint(resources_bp, url_prefix='/resources')

    # Registrar records blueprint
    from app.api.records import bp as records_bp
    app.register_blueprint(records_bp, url_prefix='/records')

    # Registrar system blueprint
    from app.api.system import bp as system_bp
    app.register_blueprint(system_bp, url_prefix='/system')

    from app.plugins.filesProcessing import ExtendedPluginClass
    from app.plugins.filesProcessing import plugin_info
    plugin_bp = ExtendedPluginClass('filesProcessing', __name__, **plugin_info)
    plugin_bp.add_route()
    app.register_blueprint(plugin_bp, url_prefix='/filesprocessing')

    return app

# definiendo celery
def celery_init_app(app: Flask) -> Celery:
    class FlaskTask(Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.config_from_object(app.config["CELERY"])
    celery_app.set_default()
    app.extensions["celery"] = celery_app
    return celery_app


app = create_app()
celery_app = celery_init_app(app)


if __name__ == '__main__':
    app.run()