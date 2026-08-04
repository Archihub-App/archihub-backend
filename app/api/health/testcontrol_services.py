import os
import secrets
import shutil
import uuid
from datetime import datetime, timezone

from celery import shared_task
from celery.result import AsyncResult
from flask import current_app as app

from app.utils import DatabaseHandler
from app.utils import CacheHandler
from app.utils.functions import find_by_id
from app.utils.TestControlAuth import TEST_MODE_MARKER_NAME
from app.version import __version__

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

TEMPORAL_FILES_PATH = os.environ.get('TEMPORAL_FILES_PATH', '')

SEED_VERSION = '1'


def get_status():
    return {
        'disposable': True,
        'app_version': __version__,
        'seed_version': SEED_VERSION,
    }, 200


def get_routes():
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            'endpoint': rule.endpoint,
            'path': str(rule),
            'methods': sorted(m for m in rule.methods if m not in ('HEAD', 'OPTIONS')),
        })
    return {'routes': routes}, 200


def _wipe_mongo():
    mongodb.delete_records('system', {'name': {'$ne': TEST_MODE_MARKER_NAME}})

    for name in mongodb.get_collections():
        if name == 'system':
            continue
        mongodb.mydb.drop_collection(name)


def _wipe_elasticsearch():
    index_management = mongodb.get_record('system', {'name': 'index_management'})
    index_activation = find_by_id(index_management['data'], 'index_activation') if index_management else None
    if not (index_activation and index_activation['value']):
        return
    try:
        from app.utils import IndexHandler
        index_handler = IndexHandler.IndexHandler()
        for alias in index_handler.get_aliases().keys():
            index_handler.delete_all_documents(alias)
    except Exception:
        pass


def _wipe_qdrant():
    index_management = mongodb.get_record('system', {'name': 'index_management'})
    vector_activation = find_by_id(index_management['data'], 'vector_activation') if index_management else None
    if not (vector_activation and vector_activation['value']):
        return
    try:
        from app.utils import VectorDatabaseHandler
        from qdrant_client import models
        vector_handler = VectorDatabaseHandler.VectorDatabaseHandler()
        for collection in (VectorDatabaseHandler.METADATA_RESOURCES, VectorDatabaseHandler.TRANSCRIPT_RECORDS):
            vector_handler.qdrant.delete(
                collection_name=collection,
                points_selector=models.FilterSelector(filter=models.Filter()),
            )
    except Exception:
        pass


def _wipe_temporal_files():
    if not TEMPORAL_FILES_PATH or not os.path.isdir(TEMPORAL_FILES_PATH):
        return
    for entry in os.listdir(TEMPORAL_FILES_PATH):
        entry_path = os.path.join(TEMPORAL_FILES_PATH, entry)
        try:
            if os.path.isdir(entry_path):
                shutil.rmtree(entry_path)
            else:
                os.remove(entry_path)
        except Exception:
            pass


def _seed_baseline(run_id):
    from app.api.system.services import set_system_setting, set_first_time

    set_system_setting()

    admin_username = 'test_admin@archihub.test'
    admin_password = secrets.token_urlsafe(18)
    seed_body = {
        'username': admin_username,
        'password': admin_password,
        'confirmPassword': admin_password,
        'typeTemplate': 'basic',
    }
    result, status = set_first_time(seed_body)
    if status != 201 and status != 200:
        raise RuntimeError(f'Seeding failed: {result}')

    return {
        'run_id': run_id,
        'admin_username': admin_username,
        'admin_password': admin_password,
    }


@shared_task(ignore_result=False, name='testcontrol.reset')
def reset_task(run_id):
    _wipe_mongo()
    _wipe_elasticsearch()
    _wipe_qdrant()
    cacheHandler.clear_cache()
    _wipe_temporal_files()

    credentials = _seed_baseline(run_id)
    credentials['completed_at'] = datetime.now(timezone.utc).isoformat()
    return credentials


def start_reset():
    from app.api.tasks.services import add_task

    run_id = str(uuid.uuid4())
    task = reset_task.delay(run_id)

    try:
        add_task(task.id, 'testcontrol.reset', 'automatic', 'msg')
    except Exception:
        pass

    return {'task_id': task.id, 'run_id': run_id}, 202


def poll_reset(task_id):
    result = AsyncResult(task_id)

    if result.state in ('PENDING', 'STARTED', 'RETRY'):
        return {'status': 'pending'}, 200

    if result.state == 'FAILURE':
        return {'status': 'failed', 'error': str(result.result)}, 200

    if result.successful():
        return {'status': 'completed', 'result': result.result}, 200

    return {'status': result.state.lower()}, 200
