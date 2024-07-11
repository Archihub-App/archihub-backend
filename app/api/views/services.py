from app.utils import CacheHandler
from app.utils import DatabaseHandler
from app.api.views.models import View, ViewUpdate
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from bson.objectid import ObjectId

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

def update_cache():
    get.invalidate_all()
    get_view_info.invalidate_all()
    get_all.invalidate_all()

@cacheHandler.cache.cache()
def get(id, user):
    view = mongodb.get_record('views', {'_id': ObjectId(id)})

    if not view:
        return {'msg': 'Vista de consulta no encontrada'}, 404
    
    view.pop('_id')

    return view, 200

@cacheHandler.cache.cache()
def get_view_info(view_slug):
    view = mongodb.get_record('views', {'slug': view_slug})

    if not view:
        return {'msg': 'Vista de consulta no encontrada'}, 404

    view.pop('_id')

    return view, 200

def update(id, body, user):
    try:
        view = ViewUpdate(**body)
        view_updated = mongodb.update_record('views', {'_id': ObjectId(id)}, view)
        update_cache()

        log = {
            'data': view_updated.raw_result
        }

        register_log(user, log_actions['view_update'], log)

        return {'msg': 'Vista de consulta actualizada exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500

@cacheHandler.cache.cache()
def get_all(user):
    views = mongodb.get_all_records('views', {}, [('name', 1), ('description', 1), ('slug', 1)])

    resp = [{ 'name': view['name'], 'id': str(view['_id']), 'description': view['description'], 'slug': view['slug'] } for view in views]

    return resp, 200

def create(body, user):
    try:
        view = View(**body)
        view_created = mongodb.insert_record('views', view)

        update_cache()

        log = {
            'data': view_created.inserted_id
        }

        register_log(user, log_actions['view_create'], log)

        return {'msg': 'Vista de consulta creada exitosamente'}, 201
    except Exception as e:
        return {'msg': str(e)}, 500
    
def delete(id, user):
    try:
        view_deleted = mongodb.delete_record('views', {'_id': ObjectId(id)})

        log = {
            'data': view_deleted.raw_result
        }

        register_log(user, log_actions['view_delete'], log)

        return {'msg': 'Vista de consulta eliminada exitosamente'}, 200
    except Exception as e:
        return {'msg': str(e)}, 500