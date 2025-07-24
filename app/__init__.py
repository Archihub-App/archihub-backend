from app.version import __version__
'''
ARCHIHUB: A comprehensive tool for organizing and connecting information
Author: BITSOL
Website: https://bit-sol.xyz/
Made with ❤️ in Colombia
'''

from flask import Flask
from flasgger import Swagger
from config import config
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from celery import Celery, Task
from celery.schedules import crontab
from flask import Flask
from flask_babel import Babel, gettext as _

import os
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from app.utils import AIHandler
from app.api.system.services import update_option, clear_cache

# leer variables de entorno desde el archivo .env
from dotenv import load_dotenv
load_dotenv()

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()
aiHandler = AIHandler.AIHandler()
scheduled_tasks = {}

def get_crontab_schedule(periodicity, hour_execution):
    try:
        hour, minute = map(int, hour_execution.split(':'))
    except (ValueError, AttributeError):
        hour, minute = 0, 0

    if periodicity == 'once_a_day':
        return crontab(hour=hour, minute=minute)
    elif periodicity == 'once_a_week':
        return crontab(hour=hour, minute=minute, day_of_week='0')
    elif periodicity == 'once_a_month':
        return crontab(hour=hour, minute=minute, day_of_month='1')
    elif periodicity == 'once_a_year':
        return crontab(hour=hour, minute=minute, day_of_month='1', month_of_year='1')
    
    return None

def create_app(config_class=config[os.environ['FLASK_ENV']]):
    from app.api.system.services import set_system_setting
    set_system_setting()
    
    app = Flask(__name__)

    app.config.from_mapping(
        CELERY=dict(
            broker_url=os.environ.get(
                "CELERY_BROKER_URL", "redis://localhost"),
            result_backend=os.environ.get(
                "CELERY_BROKER_URL", "redis://localhost"),
            task_ignore_result=True,
            broker_connection_retry_on_startup=True
        ),
    )
    app.config.from_object(config_class)
    
    # Babel
    app.config['BABEL_DEFAULT_LOCALE'] = 'en'
    app.config['BABEL_SUPPORTED_LOCALES'] = ['es', 'en']
    app.config['BABEL_TRANSLATION_DIRECTORIES'] = os.path.join(os.path.abspath(os.path.dirname(__file__)), "translations")
    
    # agregar CORS
    CORS(app, resources={
        r"/adminApi/*": {"origins": "*"},
        r"/publicApi/*": {"origins": "*"},
        r"/*": {"origins": os.environ.get('URL_FRONTEND', '*').split(',')},
    })
    
    # Inicializar JWT
    jwt = JWTManager(app)
    # Inicializar Swagger

    app.config['SWAGGER'] = {
        'title': 'ARCHIHUB: A comprehensive tool for organizing and connecting information',
        'uiversion': 3,
        'info': {
            'title': 'ARCHIHUB: A comprehensive tool for organizing and connecting information',
            'version': __version__,
            'description': 'This is the API documentation for [ArchiHub](https://www.instagram.com/archihub_app/). Additional information and general project documentation can be found [here](https://archihub-app.github.io/archihub.github.io/es/archihub/).<br /><br />Made with ❤️ in Colombia<br />',
            'termsOfService': 'https://archihub-app.github.io/archihub.github.io/es/conducta/',
            'contact': {
                'name': 'BITSOL SAS',
                'url': 'https://bit-sol.xyz/'#,
                #'email': 'bitsol@gmail.com'
            },
            'license': {
                'name': 'MIT',
                'url': 'https://archihub-app.github.io/archihub.github.io/es/licencia/'
            }
        }
    }

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

    # Registrar geosystem blueprint
    from app.api.geosystem import bp as geosystem_bp
    app.register_blueprint(geosystem_bp, url_prefix='/geosystem')
    
    # Registrar usertasks blueprint
    from app.api.usertasks import bp as usertasks_bp
    app.register_blueprint(usertasks_bp, url_prefix='/usertasks')
    
    #Registrar aiservices blueprint
    from app.api.aiservices import bp as aiservices_bp
    app.register_blueprint(aiservices_bp, url_prefix='/aiservices')

    # Helper function to find a record by ID
    from app.utils.functions import find_by_id

    admin_api = mongodb.get_record('system', {'name': 'api_activation'})

    api_activation_admin = find_by_id(admin_api['data'], 'api_activation_admin')
    if api_activation_admin and api_activation_admin['value']:
        print('-'*50)
        print('👨‍💼 🔧 📡 ' + _("Administrators API is active"))
        from app.api.adminApi import bp as adminApi_bp
        app.register_blueprint(adminApi_bp, url_prefix='/adminApi')

    api_activation_public = find_by_id(admin_api['data'], 'api_activation_public')
    if( api_activation_public and api_activation_public['value']):
        print('-'*50)
        print('🌐 🟢 🚀 ' + _("API pública activa"))
        from app.api.publicApi import bp as publicApi_bp
        app.register_blueprint(publicApi_bp, url_prefix='/publicApi')
    
    index_management = mongodb.get_record('system', {'name': 'index_management'})
    search_loaded = False

    index_activation = find_by_id(index_management['data'], 'index_activation')
    if index_activation and index_activation['value']:
        print('-'*50)
        print('🗂️  ⚙️  📊 Indexing is active')
        try:
            from app.utils import IndexHandler
            index_handler = IndexHandler.IndexHandler()
            from app.api.search import bp as search_bp
            app.register_blueprint(search_bp, url_prefix='/search')
            search_loaded = True
            from app.api.system.services import hookHandlerIndex
            hookHandlerIndex()
        except Exception as e:
            print('-'*50)
            print(str(e))
            print('No se pudo iniciar el indexador de documentos')
            index_activation['value'] = False
            update_option('index_management', {'index_activation': False})
            
    vector_activation = find_by_id(index_management['data'], 'vector_activation')
    if vector_activation and vector_activation['value']:
        try:
            from app.utils import VectorDatabaseHandler
            vector_handler = VectorDatabaseHandler.VectorDatabaseHandler()
            if not search_loaded:
                from app.api.search import bp as search_bp
                app.register_blueprint(search_bp, url_prefix='/search')
            print('-'*50)
            print('🧬 📐 📈 Vector indexing is active')
        except Exception as e:
            print('-'*50)
            print('No se pudo iniciar el indexador de vectores')
            print(str(e))
            vector_activation['value'] = False
            update_option('index_management', {'vector_activation': False})

    if os.environ.get('FLASK_ENV') == 'DEV':
        clear_cache()
        
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
    capabilities = plugin_bp.get_capabilities()
    if capabilities and 'scheduler' in capabilities:
        current = plugin_bp.get_plugin_settings()
        if current or not current == {}:
            if 'schedule_tasks' in current and len(current['schedule_tasks']) > 0:
                for task in current['schedule_tasks']:
                    if 'task' in task and 'periodicity' in task and 'hour_execution' in task:
                        label = f"{task['task']} - {task['periodicity']} - {task['hour_execution']}"
                        scheduled_tasks[label] = {
                            'task': task['task'],
                            'schedule': get_crontab_schedule(task['periodicity'], task['hour_execution'])
                        }
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
    celery_app.conf.beat_schedule = {
        *scheduled_tasks
    }
    celery_app.set_default()
    app.extensions["celery"] = celery_app
    return celery_app

app = create_app()
celery_app = celery_init_app(app)
app.celery_app = celery_app

def get_locale():
    user_management = mongodb.get_record('system', {'name': 'user_management'})
    lenguaje = user_management['data'][2]['value']
    return lenguaje
    
babel = Babel(app, locale_selector=get_locale)

banner_width = 82
version_str = f"v{__version__}"
author_str = "Author: BITSOL"
made_in_str = "Made with ❤️  in Colombia"
website_str = "Website: https://bit-sol.com.co/"

print(f'''
{':' * banner_width}
::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::
:::'###::::'########:::'######::'##::::'##:'####:'##::::'##:'##::::'##:'########::
::'## ##::: ##.... ##:'##... ##: ##:::: ##:. ##:: ##:::: ##: ##:::: ##: ##.... ##:
:'##:. ##:: ##:::: ##: ##:::..:: ##:::: ##:: ##:: ##:::: ##: ##:::: ##: ##:::: ##:
'##:::. ##: ########:: ##::::::: #########:: ##:: #########: ##:::: ##: ########::
 #########: ##.. ##::: ##::::::: ##.... ##:: ##:: ##.... ##: ##:::: ##: ##.... ##:
 ##.... ##: ##::. ##:: ##::: ##: ##:::: ##:: ##:: ##:::: ##: ##:::: ##: ##:::: ##:
 ##:::: ##: ##:::. ##:. ######:: ##:::: ##:'####: ##:::: ##:. #######:: ########::
..:::::..::..:::::..:::......:::..:::::..::....::..:::::..:::.......:::........:::
{':' * banner_width}
{version_str}{':' * (banner_width - len(version_str))}
{author_str}{':' * (banner_width - len(author_str))}
{made_in_str}{':' * (banner_width - len(made_in_str) + 1)}
{website_str}{':' * (banner_width - len(website_str))}
{':' * banner_width}
''')

if __name__ == '__main__':
    app.run(threaded=True)