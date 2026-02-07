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
import mimetypes
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

def extract_important_exif(metadata):
    if not metadata:
        return {}

    data = metadata
    keys_of_interest = {
        # 'File:FileName',
        # 'File:FileSize',
        'EXIF:Make',
        'EXIF:Model',
        'EXIF:LensModel',
        'EXIF:FocalLength',
        'EXIF:ApertureValue',
        'EXIF:FNumber',
        'EXIF:ExposureTime',
        'EXIF:ISO',
        # 'EXIF:DateTimeOriginal',
        # 'EXIF:CreateDate',
        # 'EXIF:ModifyDate',
        'EXIF:ImageWidth',
        'EXIF:ImageHeight',
        'Composite:Megapixels',
        # 'Composite:FOV',
        # 'Composite:ShutterSpeed',
        # 'XMP:Software',
        # 'XMP:Firmware',
        # 'XMP:ApproximateFocusDistance',
        # 'XMP:WhiteBalance',
        # 'XMP:Exposure2012',
        # 'XMP:Contrast2012',
        # 'XMP:Highlights2012',
        # 'XMP:Shadows2012',
        # 'XMP:Vibrance',
        # 'XMP:Saturation',
    }

    cleaned = {k: data[k] for k in keys_of_interest if k in data}
    return cleaned


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
            return {'msg': _('Record does not exist')}, 404
        
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
                if 'metadata' in record['processing'][key]:
                    keys[key]['metadata'] = extract_important_exif(record['processing'][key]['metadata'])

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
            for p in record['parent']:
                if 'id' in p:
                    p['id'] = str(p['id'])
                    from app.api.resources.services import get_accessRights
                    p['accessRights'] = get_accessRights(p['id'])
                    if p['accessRights'] != None:
                        return {'msg': _('You do not have permission to view this record')}, 401


        if 'parents' in record:
            record.pop('parents')

        # Si el record existe, retornar el record
        return parse_result(record), 200

    except Exception as e:
        return {'msg': str(e)}, 500
    
def _parse_ms(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_partial_response(path, start_byte, end_byte):
    size = os.path.getsize(path)
    if start_byte < 0:
        start_byte = 0
    if end_byte >= size:
        end_byte = size - 1
    if end_byte < start_byte:
        return None

    length = end_byte - start_byte + 1
    with open(path, 'rb') as f:
        f.seek(start_byte)
        data = f.read(length)

    mimetype, _ = mimetypes.guess_type(path)
    resp = Response(data, 206, mimetype=mimetype or 'application/octet-stream', direct_passthrough=True)
    resp.headers['Content-Range'] = f'bytes {start_byte}-{end_byte}/{size}'
    resp.headers['Accept-Ranges'] = 'bytes'
    resp.headers['Content-Length'] = str(length)
    return resp


def _get_video_byte_range(path, metadata, start_ms, end_ms):
    if not metadata:
        return None
    duration_ms = metadata.get('duration_ms')
    if not duration_ms:
        return None
    try:
        duration_ms = float(duration_ms)
    except (TypeError, ValueError):
        return None
    if duration_ms <= 0:
        return None

    size = os.path.getsize(path)
    bit_rate = metadata.get('bit_rate')
    try:
        bit_rate = float(bit_rate) if bit_rate is not None else None
    except (TypeError, ValueError):
        bit_rate = None

    if bit_rate and bit_rate > 0:
        bytes_per_ms = bit_rate / 8.0 / 1000.0
        start_byte = int(start_ms * bytes_per_ms)
        end_byte = int(end_ms * bytes_per_ms) - 1
    else:
        start_byte = int((start_ms / duration_ms) * size)
        end_byte = int((end_ms / duration_ms) * size) - 1

    return start_byte, end_byte


def get_stream(id, size='large', start_ms=None, end_ms=None):
    try:
        resp_, status = get_by_id(id, True)
        if status != 200:
            return resp_, status

        path, type = cache_get_record_stream(id)
        
        path = os.path.join(WEB_FILES_PATH, path)

        if type == 'video':
            path = path + '.mp4'
        elif type == 'audio':
            path = path + '.mp3'
        elif type == 'image':
            if size == 'large':
                path = path + '_large.jpg'
            elif size == 'medium':
                path = path + '_medium.jpg'
            else:
                path = path + '_small.jpg'

        start_val = _parse_ms(start_ms)
        end_val = _parse_ms(end_ms)
        if type == 'video' and (start_ms is not None or end_ms is not None):
            if start_val is None or end_val is None:
                return {'msg': _('Invalid start_ms or end_ms')}, 400
            if start_val < 0 or end_val <= start_val:
                return {'msg': _('Invalid start_ms or end_ms')}, 400

            processing = resp_.get('processing', {})
            metadata = processing.get('fileProcessing', {}).get('metadata', {})
            byte_range = _get_video_byte_range(path, metadata, start_val, end_val)
            if byte_range:
                response = _build_partial_response(path, byte_range[0], byte_range[1])
                if response:
                    return response

        return send_file(path, as_attachment=True)

    except Exception as e:
        return {'msg': str(e)}, 500

def get_transcription(id, slug):
    try:
        resp_, status = get_by_id(id)
        if status != 200:
            return resp_, status

        resp = cache_get_record_transcription(id, slug)
        # Si el record existe, retornar el record
        return resp, 200

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