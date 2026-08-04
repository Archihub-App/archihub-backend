from app.utils import DatabaseHandler
from app.utils import CacheHandler
from app.utils.functions import find_by_id
from flask import current_app as app

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()


def check_mongo():
    try:
        mongodb.myclient.admin.command('ping')
        return True, None
    except Exception as e:
        return False, str(e)


def check_redis():
    try:
        cacheHandler.cache.client.ping()
        return True, None
    except Exception as e:
        return False, str(e)


def check_elasticsearch():
    index_management = mongodb.get_record('system', {'name': 'index_management'})
    index_activation = find_by_id(index_management['data'], 'index_activation') if index_management else None
    if not (index_activation and index_activation['value']):
        return 'disabled', None
    try:
        from app.utils import IndexHandler
        index_handler = IndexHandler.IndexHandler()
        index_handler.get_aliases()
        return True, None
    except Exception as e:
        return False, str(e)


def check_qdrant():
    index_management = mongodb.get_record('system', {'name': 'index_management'})
    vector_activation = find_by_id(index_management['data'], 'vector_activation') if index_management else None
    if not (vector_activation and vector_activation['value']):
        return 'disabled', None
    try:
        from app.utils import VectorDatabaseHandler
        vector_handler = VectorDatabaseHandler.VectorDatabaseHandler()
        vector_handler.qdrant.get_collections()
        return True, None
    except Exception as e:
        return False, str(e)


def check_celery():
    try:
        pings = app.celery_app.control.ping(timeout=1.0)
        if not pings:
            return False, 'No Celery worker responded'
        return True, None
    except Exception as e:
        return False, str(e)


def get_readiness():
    checks = {
        'mongo': check_mongo(),
        'redis': check_redis(),
        'elasticsearch': check_elasticsearch(),
        'qdrant': check_qdrant(),
        'celery': check_celery(),
    }

    result = {}
    all_ready = True
    for name, (status, error) in checks.items():
        result[name] = {'status': status if isinstance(status, str) else ('ok' if status else 'error')}
        if error:
            result[name]['error'] = error
        if status is False:
            all_ready = False

    return {'ready': all_ready, 'checks': result}, (200 if all_ready else 503)
