from app.api.snaps.services import get_document_snap, get_image_snap
from flask_babel import _
from bson.objectid import ObjectId
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from app.api.records.public_services import get_by_id as get_by_id_record

cacheHandler = CacheHandler.CacheHandler()
mongodb = DatabaseHandler.DatabaseHandler()

def get_by_id(id):
    try:
        # Obtener el snap por su id
        snap = mongodb.get_record('snaps', {'_id': ObjectId(id)}, {'user': 1, 'record_id': 1, 'data': 1, 'type': 1})
        
        # Si el snap no existe, retornar error
        if snap is None:
            return {'msg': _('Snap not found')}, 404
        
        record, status = get_by_id_record(snap['record_id'])
        
        if status != 200:
            return record , 500
        
        if snap['type'] == 'document':
            return get_document_snap(None, snap['record_id'], snap['data'])
        elif snap['type'] == 'image':
            return get_image_snap(None, snap['record_id'], snap['data'])

        snap['_id'] = str(snap['_id'])
        
        # Retornar snap
        return snap, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
    
def update_cache():
    get_by_id.invalidate_all()