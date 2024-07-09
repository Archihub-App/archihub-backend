'''
ARCHIHUB herramienta de gestión documental
Versión 0.2
Autor: Néstor Andrés Peña
Hecho con <3 en Colombia
'''

from flask import Flask
from flasgger import Swagger
from config import config
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from celery import Celery
from celery import Task
from flask import Flask

import os
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from app.utils import IndexHandler
from app.api.system.services import update_option

# leer variables de entorno desde el archivo .env
from dotenv import load_dotenv
load_dotenv()

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

def create_app(config_class=config[os.environ['FLASK_ENV']]):
    app = Flask(__name__)

    app.config.from_mapping(
        CELERY=dict(
            broker_url=os.environ.get(
                "CELERY_BROKER_URL", "redis://localhost"),
            result_backend=os.environ.get(
                "CELERY_BROKER_URL", "redis://localhost"),
            task_ignore_result=True,
        ),
    )
    app.config.from_object(config_class)

    # agregar CORS
    CORS(app)
    # CORS(app, resources={r"/*": {"origins": "*"}})
    # Inicializar JWT
    jwt = JWTManager(app)
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

    # registrar plugins activos en la base de datos
    plugins = mongodb.get_record('system', {'name': 'active_plugins'})
    for p in plugins['data']:
        register_plugin(app, p, p)

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

    # Registrar tasks blueprint
    from app.api.tasks import bp as tasks_bp
    app.register_blueprint(tasks_bp, url_prefix='/tasks')

    # Registrar views blueprint
    from app.api.views import bp as views_bp
    app.register_blueprint(views_bp, url_prefix='/views')

    # Registrar snaps blueprint
    from app.api.snaps import bp as snaps_bp
    app.register_blueprint(snaps_bp, url_prefix='/snaps')

    # verificar en la base de datos si la admin API está activa
    admin_api = mongodb.get_record('system', {'name': 'api_activation'})

    if(admin_api['data'][0]['value']):
        print('Administrators API is active')
        from app.api.adminApi import bp as adminApi_bp
        app.register_blueprint(adminApi_bp, url_prefix='/adminApi')

    if(admin_api['data'][1]['value']):
        print('Public API is active')
        from app.api.publicApi import bp as publicApi_bp
        app.register_blueprint(publicApi_bp, url_prefix='/publicApi')
    
    index_management = mongodb.get_record('system', {'name': 'index_management'})

    if index_management['data'][0]['value']:
        print('Indexing is active')
        try:
            index_handler = IndexHandler.IndexHandler()
            from app.api.search import bp as search_bp
            app.register_blueprint(search_bp, url_prefix='/search')
            index_handler.start()
            from app.api.system.services import hookHandlerIndex
            hookHandlerIndex()
        except:
            print('No se pudo iniciar el indexador de documentos')
            index_management['data'][0]['value'] = False
            update_option('index_management', {'index_activation': False})


    return app

# función para registrar plugins de forma dinámica
def register_plugin(app, plugin_name, plugin_url_prefix):
    plugin_module = __import__(f'app.plugins.{plugin_name}', fromlist=[
                               'ExtendedPluginClass', 'plugin_info'])
    plugin_bp = plugin_module.ExtendedPluginClass(
        plugin_name, __name__, **plugin_module.plugin_info)
    plugin_bp.add_routes()
    plugin_bp.get_image()
    plugin_bp.get_settings()
    if os.environ.get('CELERY_WORKER', False):
        plugin_bp.activate_settings()
    app.register_blueprint(plugin_bp, url_prefix=f'/{plugin_url_prefix}')

# definiendo celery
def celery_init_app(app: Flask) -> Celery:
    class FlaskTask(Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.config_from_object(app.config["CELERY"])
    celery_app.conf.timezone = 'UTC'
    celery_app.conf.update(
        CELERYD_CONCURRENCY=int(os.environ.get("CELERYD_CONCURRENCY", 1)),
        CELERYD_PREFETCH_MULTIPLIER=1,
        CELERY_ACKS_LATE=True
    )
    celery_app.set_default()
    app.extensions["celery"] = celery_app
    return celery_app

app = create_app()
celery_app = celery_init_app(app)
app.celery_app = celery_app

if __name__ == '__main__':
    app.run()
