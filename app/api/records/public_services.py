import datetime
from flask import jsonify, send_file, Response
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from bson import json_util
import json
from bson.objectid import ObjectId
from app.api.records.models import Record as FileRecord
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.api.users.services import has_right
from app.api.records.models import RecordUpdate as FileRecordUpdate
from app.utils.functions import cache_get_record_stream, cache_get_record_transcription, cache_get_record_document_detail, cache_get_pages_by_id, cache_get_block_by_page_id, cache_get_imgs_gallery_by_id, cache_get_processing_metadata
from werkzeug.utils import secure_filename
import os
import shutil
import hashlib
import magic
import uuid
from dotenv import load_dotenv
from flask_babel import _
load_dotenv()

ORIGINAL_FILES_PATH = os.environ.get('ORIGINAL_FILES_PATH', '')
WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')

if not os.path.exists(ORIGINAL_FILES_PATH):
    os.makedirs(ORIGINAL_FILES_PATH)


ALLOWED_EXTENSIONS = set(['txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'oga', 'ogg', 'ogv',
                          'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'csv', 'zip', 'rar', '7z', 'mp4',
                          'mp3', 'wav', 'avi', 'mkv', 'flv', 'mov', 'wmv', 'm4a', 'mxf', 'cr2', 'arw', 'mts', 'nef', 'json', 'html', 'wma', 'aac', 'flac'])

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

def update_cache():
    get_by_id.invalidate_all()

def parse_result(result):
    return json.loads(json_util.dumps(result))

def get_document_gallery(id, pages, size):
    try:
        from app.api.resources.public_services import get_by_id as get_resource_by_id
        resp_, status = get_resource_by_id(id)
        if status != 200:
            return resp_, status
        
        pages = json.dumps(pages)
        resp = cache_get_imgs_gallery_by_id(id, pages, size)
        response = Response(json.dumps(resp).encode('utf-8'), mimetype='application/json', direct_passthrough=False)
        return response
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500

def get_by_index_gallery(body):
    try:
        if 'id' not in body:
            return {'msg': _('id not defined')}, 400
        if 'index' not in body:
            return {'msg': _('index not defined')}, 400
        
        resource = mongodb.get_record('resources', {'_id': ObjectId(body['id'])}, fields={'filesObj': 1})
        ids = []
        if 'filesObj' in resource:
            for r in resource['filesObj']:
                ids.append(r['id'])

        img = list(mongodb.get_all_records('records', {'_id': {'$in': [ObjectId(id) for id in ids]}, 'processing.fileProcessing.type': 'image'}, fields={'processing': 1}))
        
        order_dict = {file['id']: file['order'] if 'order' in file else 0 for file in resource['filesObj']}

        img_sorted = sorted(img, key=lambda x: order_dict.get(x['_id'], float('inf')))

        img = img_sorted
        
        img = img[body['index']:body['index'] + 1]

        return get_by_id(str(img[0]['_id']))

    except Exception as e:
        return {'msg': str(e)}, 500

@cacheHandler.cache.cache(limit=5000)
def get_by_id(id, fullFields = False):
    try:
        # Buscar el record en la base de datos
        record = mongodb.get_record('records', {'_id': ObjectId(id)}, fields={'parent': 1, 'parents': 1, 'accessRights': 1, 'hash': 1, 'processing': 1, 'name': 1, 'displayName': 1, 'size': 1, 'filepath': 1})

        # Si el record no existe, retornar error
        if not record:
            record = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'_id': 1})

            if not record:
                return {'msg': _('Record not found')}, 404
            else:
                return parse_result(record), 200
        
        if 'accessRights' in record:
            if record['accessRights']:
                return {'msg': _('You don\'t have the required authorization')}, 401
        
        # get keys from record['processing']
        keys = {}
        fileProcessing = None
        if 'processing' in record and not fullFields:
            # iterate over processing keys in record['processing']
            for key in record['processing']:
                keys[key] = {}
                keys[key]['type'] = record['processing'][key]['type']

            record['processing'] = keys

        if not fullFields:
            record.pop('filepath', None)

        from app.api.types.services import get_icon

        if 'parent' in record:
            to_clean = []
            for p in record['parent']:
                r_ = mongodb.get_record('resources', {'_id': ObjectId(p['id'])}, fields={'metadata.firstLevel.title': 1, 'post_type': 1})
                if r_:
                    p['name'] = r_['metadata']['firstLevel']['title']
                    p['icon'] = get_icon(r_['post_type'])
                else:
                    to_clean.append(p['id'])

            record['parent'] = [x for x in record['parent'] if x['id'] not in to_clean]


        if 'parents' in record:
            record.pop('parents')

        # Si el record existe, retornar el record
        return parse_result(record), 200

    except Exception as e:
        return {'msg': str(e)}, 500
    
def download_records(body):
    try:
        if 'id' not in body:
            return {'msg': _('id not defined')}, 400
        
        resp_, status = get_by_id(body['id'], True)
        if status != 200:
            return resp_, status
        
        record = resp_
        if 'processing' not in record:
            return {'msg': _('Record does not have processing')}, 404
        
        if 'fileProcessing' not in record['processing']:
            return {'msg': _('Record does not have fileProcessing')}, 404
        
        if 'type' not in record['processing']['fileProcessing']:
            return {'msg': _('Record does not have fileProcessing type')}, 404
                
        path = os.path.join(WEB_FILES_PATH, record['processing']['fileProcessing']['path'])
        
        if record['processing']['fileProcessing']['type'] == 'image':
            path = path + '_large.jpg'
        elif record['processing']['fileProcessing']['type'] == 'audio':
            path = path + '.mp3'
        elif record['processing']['fileProcessing']['type'] == 'video':
            path = path + '.mp4'
        elif record['processing']['fileProcessing']['type'] == 'document':
            path = os.path.join(ORIGINAL_FILES_PATH, record['filepath'])
            
        if body['type'] == 'original':
            path = os.path.join(ORIGINAL_FILES_PATH, record['filepath'])
            return send_file(path, as_attachment=True)
        elif body['type'] == 'small':
            return send_file(path, as_attachment=True)
        
    except Exception as e:
        return {'msg': str(e)}, 500