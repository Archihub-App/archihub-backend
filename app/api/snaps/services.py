from flask import jsonify, request, Response, send_file
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from bson import json_util
import json
from app.api.snaps.models import Snap
from app.api.snaps.models import SnapUpdate
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from bson.objectid import ObjectId
from app.utils.functions import get_roles, get_access_rights, get_roles_id, get_access_rights_id
from datetime import datetime
from io import BytesIO
from PIL import Image
import base64
import os
from flask_babel import _

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

def update_cache():
    get_by_user_id.invalidate_all()
    
# Nuevo servicio para crear un snap
def create(user, body):
    try:
        record = mongodb.get_record('records', {'_id': ObjectId(body['record_id'])}, {'name': 1})
        if record is None:
            return {'msg': _('Record not found')}, 404
        
        snap = {
            'user': user,
            'record_id': body['record_id'],
            'record_name': record['name'],
            'type': body['type'],
            'data': body['data'],
            'createdAt': datetime.now(),
        }

        print(snap)

        snap = Snap(**snap)
        # Insertar el snap en la base de datos
        new_snap = mongodb.insert_record('snaps', snap)
        # Registrar el log
        register_log(user, log_actions['snap_create'], {'snap': {
            'id': str(new_snap.inserted_id),
        }})
        # Limpiar la cache
        update_cache()

        return {'msg': _('Snap created successfully')}, 201
    except Exception as e:
        return {'msg': str(e)}, 500
    

# Nuevo servicio para actualizar un snap por su id
def update_by_id(id, body, user):
    try:
        # Obtener el snap por su id
        snap = mongodb.get_record_by_id('snaps', id)
        # Si el snap no existe, retornar error
        if snap is None:
            return {'msg': 'Snap no encontrado'}, 404
        # Crear instancia de SnapUpdate con el body del request
        snap_update = SnapUpdate(**body)
        # Actualizar el snap en la base de datos
        mongodb.update_record('snaps', {'_id': ObjectId(id)}, snap_update)
        # Registrar el log
        register_log(user, log_actions['snap_update'], {'snap': {
            'name': snap['name'],
            'id': id,
        }})
        # Limpiar la cache
        update_cache()
        return {'msg': _('Snap updated successfully')}, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para eliminar un snap por su id
def delete_by_id(id, user):
    try:
        # Obtener el snap por su id
        snap = mongodb.get_record('snaps', {'_id': ObjectId(id)}, {'user': 1})
        # Si el snap no existe, retornar error
        if snap is None:
            return {'msg': _('Snap not found')}, 404
        if snap['user'] != user:
            return {'msg': _('You don\'t have the required authorization')}, 401
        
        # Eliminar el snap de la base de datos
        mongodb.delete_record('snaps', {'_id': ObjectId(id)})
        # Registrar el log
        register_log(user, log_actions['snap_delete'], {'snap': {
            'id': id,
        }})
        # Limpiar la cache
        update_cache()
        return {'msg': _('Snap deleted successfully')}, 204
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para obtener todos los snaps de un usuario
@cacheHandler.cache.cache()
def get_by_user_id(user, body):
    try:
        
        resp = {}
        # Obtener todos los snaps de un usuario
        snaps = list(mongodb.get_all_records('snaps', {'user': user, 'type': body['type']}, fields={'data': 0, 'createdAt': 0}, sort=[('createdAt', -1)], limit=20, skip=body['page'] * 20))

        total = mongodb.count('snaps', {'user': user, 'type': body['type']})

        for s in snaps:
            s['_id'] = str(s['_id'])

        resp['results'] = snaps
        resp['total'] = total

        # Retornar snaps
        return resp, 200
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500

# Nuevo servicio para obtener un snap por su id
def get_by_id(id, user):
    try:
        # Obtener el snap por su id
        snap = mongodb.get_record('snaps', {'_id': ObjectId(id)}, {'user': 1, 'record_id': 1, 'data': 1, 'type': 1})
        # Si el snap no existe, retornar error
        if snap is None:
            return {'msg': _('Snap not found')}, 404
        if snap['user'] != user:
            return {'msg': _('You don\'t have the required authorization')}, 401
        
        if snap['type'] == 'document':
            return get_document_snap(user, snap['record_id'], snap['data'])
        elif snap['type'] == 'image':
            return get_image_snap(user, snap['record_id'], snap['data'])
        elif snap['type'] == 'video':
            return get_video_snap(user, snap['record_id'], snap['data'])

        snap['_id'] = str(snap['_id'])
        
        # Retornar snap
        return snap, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
def get_video_snap(user, record_id, data):
    if user:
        from app.api.records.services import get_by_id, get_stream
        record, status = get_by_id(record_id, user)
        if status != 200:
            return {'msg': _(u'Error while getting the file: {error}', error = record['msg'])} , 500
        
        print(data)
        return get_stream(record_id, user, start_ms=data.get('begin'), end_ms=data.get('end'))
    else:
        from app.api.records.public_services import get_by_id as get_by_id_public
        from app.api.records.public_services import get_stream as get_stream_public
        record, status = get_by_id_public(record_id)
        if status != 200:
            return {'msg': _(u'Error while getting the file: {error}', error = record['msg'])} , 500
        return get_stream_public(record_id, start_ms=data.get('begin'), end_ms=data.get('end'))
    
def get_document_snap(user, record_id, data):
    
    if user:
        from app.api.records.services import get_by_id
        record, status = get_by_id(record_id, user)
        if status != 200:
            return {'msg': _(u'Error while getting the file: {error}', error = record['msg'])} , 500
    else:
        from app.api.records.public_services import get_by_id as get_by_id_public
        record, status = get_by_id_public(record_id)
        if status != 200:
            return {'msg': _(u'Error while getting the file: {error}', error = record['msg'])} , 500
    
    pages = json.dumps([data['page'] - 1])
    from app.utils.functions import cache_get_pages_by_id
    resp = cache_get_pages_by_id(record_id, pages, 'big')
    img_data = resp[0]['data']
    img_data = base64.b64decode(img_data)

    image = Image.open(BytesIO(img_data))
    width, height = image.size
    aspect_ratio = width / height

    left = width * data['bbox']['x']
    top = height * data['bbox']['y']
    right = width * (data['bbox']['x'] + data['bbox']['width'])
    bottom = height * (data['bbox']['y'] + data['bbox']['height'])
    image = image.crop((left, top, right, bottom))

    img_io = BytesIO()
    image.save(img_io, 'JPEG', quality=70)
    img_io.seek(0)

    return send_file(img_io, mimetype='image/jpeg')

def get_image_snap(user, record_id, data):
    if user:
        from app.api.records.services import get_by_id
        record, status = get_by_id(record_id, user)
        if status != 200:
            return {'msg': _(u'Error while getting the file: {error}', error = record['msg'])} , 500
    else:
        from app.api.records.public_services import get_by_id as get_by_id_public
        record, status = get_by_id_public(record_id)
        if status != 200:
            return {'msg': _(u'Error while getting the file: {error}', error = record['msg'])} , 500
    
    file = mongodb.get_record('records', {'_id': ObjectId(record_id)}, {'processing': 1})
    if file is None:
        return {'msg': _('File not found')}, 404
    if 'processing' not in file:
        return {'msg': _('File not found')}, 404
    if 'fileProcessing' not in file['processing']:
        return {'msg': _('File not found')}, 404
    if 'type' not in file['processing']['fileProcessing']:
        return {'msg': _('File not found')}, 404
    if file['processing']['fileProcessing']['type'] != 'image':
        return {'msg': _('File not found')}, 404

    path = file['processing']['fileProcessing']['path']
    print(record_id, path)
    path_img = os.path.join(WEB_FILES_PATH, path)
    path_img = path_img + '_large.jpg'

    if not os.path.exists(path_img):
        return {'msg': _('File not found')}, 404

    image = Image.open(path_img)
    width, height = image.size
    aspect_ratio = width / height

    left = width * data['bbox']['x']
    top = height * data['bbox']['y']
    right = width * (data['bbox']['x'] + data['bbox']['width'])
    bottom = height * (data['bbox']['y'] + data['bbox']['height'])
    image = image.crop((left, top, right, bottom))

    img_io = BytesIO()
    image.save(img_io, 'JPEG', quality=70)
    img_io.seek(0)

    return send_file(img_io, mimetype='image/jpeg')
    
