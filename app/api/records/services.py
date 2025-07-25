import datetime
from flask import jsonify, send_file, Response
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from app.utils import HookHandler
from bson import json_util
import json
from bson.objectid import ObjectId
from app.api.records.models import Record as FileRecord
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.api.users.services import has_right
from app.api.records.models import RecordUpdate as FileRecordUpdate
from app.utils.functions import cache_get_record_stream, cache_get_record_transcription, cache_get_record_document_detail, cache_get_pages_by_id, cache_get_block_by_page_id, cache_get_imgs_gallery_by_id, cache_get_processing_metadata, has_role, cache_get_processing_result
from werkzeug.utils import secure_filename
import os
import shutil
import hashlib
import magic
import uuid
from dotenv import load_dotenv
from flask_babel import _
import re
load_dotenv()

ORIGINAL_FILES_PATH = os.environ.get('ORIGINAL_FILES_PATH', '')
WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')

if not os.path.exists(ORIGINAL_FILES_PATH):
    os.makedirs(ORIGINAL_FILES_PATH)


ALLOWED_EXTENSIONS = set(['txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'oga', 'ogg', 'ogv', 'tif', 'tiff', 'heic',
                          'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'csv', 'zip', 'rar', '7z', 'mp4',
                          'mp3', 'wav', 'avi', 'mkv', 'flv', 'mov', 'wmv', 'm4a', 'mxf', 'cr2', 'arw', 'mts', 'nef', 'json', 'html', 'wma', 'aac', 'flac'])

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()
hookHandler = HookHandler.HookHandler()

def update_cache():
    get_total.invalidate_all()
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

def allowedFile(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def set_record_metadata(record, metadata):
    if 'metadata' not in record:
        record['metadata'] = {}
    
    for key, value in metadata.items():
        record['metadata'][key] = value
    
    return record


def get_by_filters(body, current_user):
    try:
        # Buscar el recurso en la base de datos
        records = list(mongodb.get_all_records(
            'records', body['filters'], limit=20, skip=body['page'] * 20))
        # Si el recurso no existe, retornar error
        if not records:
            return {'msg': _('Record does not exist')}, 404
        
        total = get_total(json.dumps(body['filters']))


        for r in records:
            r['id'] = str(r['_id'])
            r.pop('_id')
            r['total'] = total

        # registrar el log
        register_log(current_user, log_actions['record_get_all'], {
                     'filters': body['filters']})
        # retornar los records
        return parse_result(records), 200

    except Exception as e:
        return {'msg': str(e)}, 500
    
# Funcion para obtener el total de recursos
@cacheHandler.cache.cache(limit=1000)
def get_total(obj):
    try:
        # convertir string a dict
        obj = json.loads(obj)
        # Obtener el total de recursos
        total = mongodb.count('records', obj)
        # Retornar el total
        return total
    except Exception as e:
        raise Exception(str(e))
    
# Nuevos servicio para actualizar los campos displayName y accessRights de un record
def update_record(record, user):
    try:
        update = {}
        if 'displayName' in record:
            update['displayName'] = record['displayName']
        if 'accessRights' in record:
            if record['accessRights'] != 'public':
                update['accessRights'] = record['accessRights']
            else:
                update['accessRights'] = None

        update_record_by_id(record['id'], user, update)
    except Exception as e:
        raise Exception(str(e))


def update_record_by_id(id, current_user, body):
    try:
        # Buscar el record en la base de datos
        record = mongodb.get_record('records', {'_id': ObjectId(id)})

        # Si el record no existe, retornar error
        if not record:
            raise Exception(_('Record does not exist'))

        body['updatedBy'] = current_user if current_user else 'system'
        body['updatedAt'] = datetime.datetime.now()
        # Si el record existe, actualizarlo
        update = FileRecordUpdate(**body)

        mongodb.update_record('records', {'_id': ObjectId(id)}, update)
        
        register_log(current_user, log_actions['record_update'], {
                        'record': id})
        get_by_id.invalidate_all()
    
        payload = body
        payload['_id'] = id
        hookHandler.call('record_update', payload)

        # Retornar el resultado
        return {'msg': _('Record updated')}, 200

    except Exception as e:
        raise Exception(str(e))
    
# Nuevo servicio para borrar un parent de un record
def delete_parent(resource_id, parent_id, current_user):
    try:
        # Buscar el record en la base de datos
        record = mongodb.get_record('records', {'_id': ObjectId(parent_id)})

        # Si el record no existe, retornar error
        if not record:
            return {'msg': _('Record does not exist')}, 404
        
        # Si el record no tiene el recurso como parent, retornar error
        if not any(x['id'] == resource_id for x in record['parent']):
            return {'msg': _('Record does not have the resource as parent')}, 404

        # Si el record tiene el recurso como parent, eliminarlo
        # el parent es de tipo dict y tiene los campos id y post_type
        record['parent'] = [x for x in record['parent']
                            if x['id'] != resource_id]
        
        array_parent = set(x['id'] for x in record['parent'])

        array_parents_temp = []
        # iterar sobre parent y en un nuevo array ir guardando los padres de cada parent
        for p in array_parent:
            r = mongodb.get_record('resources', {'_id': ObjectId(p)})

            if r:
                # se agregan los parents a array_parents si no estan ya en el array. Cada parent en el array_parents es del tipo {id: id, post_type: post_type}
                for parent in r['parents']:
                    array_parents_temp.append(parent)

        # se eliminan los parents que esten duplicados. Cada parent es del tipo {id: id, post_type: post_type}. Se eliminan los duplicados por id
        unique_array_parents = set(x['id'] for x in array_parents_temp)

        new_list = [next(item for item in array_parents_temp if item['id'] == id)
                    for id in unique_array_parents]
        array_parents = new_list

        status = record['status']
        # Si el record no tiene parents, cambiar el status a deleted
        if len(record['parent']) == 0:
            status = 'deleted'
            
        # Actualizar el record
        update = FileRecordUpdate(**{
            'parent': array_parent,
            'parents': array_parents,
            'status': status,
            'updatedBy': current_user if current_user else 'system',
            'updatedAt': datetime.datetime.now()
        })

        mongodb.update_record('records', {'_id': ObjectId(parent_id)}, update)

        # Registrar el log
        register_log(current_user, log_actions['record_update'], {
                     'record': parent_id})
        
        # Retornar el resultado
        return {'msg': _('Parent deleted')}, 200

    except Exception as e:
        return {'msg': str(e)}, 500


def update_parent(parent_id, current_user, parents):
    unique_array_parents = set(x['id'] for x in parents)

    new_list = [next(item for item in parents if item['id'] == id)
                for id in unique_array_parents]

    update = {
        'parents': new_list
    }

    update_record_by_id(parent_id, current_user, update)


# Nuevo servicio para crear un record para un recurso
def create(resource_id, current_user, files, upload = True, filesTags = None):
    # Buscar el recurso en la base de datos
    resource = mongodb.get_record('resources', {'_id': ObjectId(resource_id)}, fields={'parents': 1, 'post_type': 1})
    # Si el recurso no existe, retornar error
    if not resource:
        raise Exception(_('Resource does not exist'))
    
    resp = []
    index = 0

    for f in files:
        if type(f) is not dict:
            filename = secure_filename(f.filename)
        else:
            filename = f['filename']
        
        if allowedFile(filename):
            print(f)
            if upload:
                # generar un nombre unico para el archivo
                filename_new = str(uuid.uuid4()) + '.' + \
                    filename.rsplit('.', 1)[1].lower()
                # coger la fecha actual y convertirla a string de la forma YYYY/MM/DD
                date = datetime.datetime.now().strftime("%Y/%m/%d")
                # hacer un path en base a la fecha actual
                path = os.path.join(ORIGINAL_FILES_PATH, date)
                # crear el directorio para guardar el archivo usando la ruta date
                if not os.path.exists(path):
                    os.makedirs(path)

                if type(f) is not dict:
                    f.save(os.path.join(path, filename))

                    f.flush()
                    os.fsync(f.fileno())
                else:
                    shutil.copy(f['file'], os.path.join(path, filename))

                # renombrar el archivo
                os.rename(os.path.join(path, filename),
                            os.path.join(path, filename_new))
                # calcular el hash 256 del archivo
                hash = hashlib.sha256()
                with open(os.path.join(path, filename_new), 'rb') as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hash.update(chunk)

                # se verifica si el hash del archivo ya existe en la base de datos
                record = get_hash(str(hash.hexdigest()))
            else:
                record = None

            # si el record existe, se agrega el recurso como padre
            if record:
                # eliminar el archivo que se subio
                os.remove(os.path.join(path, filename_new))
                
                obj_resp = {
                    'id': str(record['_id']),
                    'tag': filesTags[index]['filetag']
                }
                if 'order' in filesTags[index]:
                    obj_resp['order'] = filesTags[index]['order']
                resp.append(obj_resp)
                
                new_parent = [{
                    'id': resource_id,
                    'post_type': resource['post_type']
                }, *record['parent']]
                # remove duplicates from new_parent
                unique_array_parents = set(x['id'] for x in new_parent)
                new_list = [next(item for item in new_parent if item['id'] == id)
                            for id in unique_array_parents]
                new_parent = new_list

                new_parents = [*resource['parents'], *record['parents']]
                unique_array_parents = set(x['id'] for x in new_parents)
                new_list = [next(item for item in new_parents if item['id'] == id)
                            for id in unique_array_parents]
                new_parents = new_list

                update_dict = {
                    'parent': new_parent,
                    'parents': new_parents
                }

                if record['status'] == 'deleted':
                    if 'processing' in record:
                        if 'files' in record['processing']:
                            if len(record['processing']['files']) > 0:
                                update_dict['status'] = 'processed'
                            else:
                                update_dict['status'] = 'uploaded'
                        else:
                            update_dict['status'] = 'uploaded'
                    else:
                        update_dict['status'] = 'uploaded'

                update_dict['updatedBy'] = current_user if current_user else 'system'
                update_dict['updatedAt'] = datetime.datetime.now()
                
                # actualizar el record
                update = FileRecordUpdate(**update_dict)
                result = mongodb.update_record(
                    'records', {'_id': ObjectId(record['_id'])}, update)
                
                # registrar el log
                register_log(current_user, log_actions['record_update'], {
                                'record': str(record['_id'])})
                
                payload = update_dict
                payload['_id'] = str(record['_id'])
                hookHandler.call('record_update_parent', payload)
                # limpiar la cache
                get_by_id.invalidate_all()
            else:
                if upload:
                    # obtener el tamaño del archivo
                    size = os.path.getsize(os.path.join(path, filename_new))

                    # usar magic para obtener el tipo de archivo
                    mime = magic.from_file(os.path.join(
                        path, filename_new), mime=True)

                    # crear un nuevo record
                    record = FileRecord(**{
                        'name': filename,
                        'hash': str(hash.hexdigest()),
                        'size': size,
                        'filepath': str(os.path.join(date, filename_new)),
                        'mime': mime,
                        'parent': [{
                            'id': resource_id,
                            'post_type': resource['post_type']
                        }],
                        'parents': resource['parents'],
                        'status': 'uploaded',
                        'updatedBy': current_user,
                        'updatedAt': datetime.datetime.now(),
                    })
                    # insertar el record en la base de datos
                    new_record = mongodb.insert_record('records', record)
                    obj_resp = {
                        'id': str(new_record.inserted_id),
                        'tag': filesTags[index]['filetag']
                    }
                    if 'order' in filesTags[index]:
                        obj_resp['order'] = filesTags[index]['order']
                    resp.append(obj_resp)
                else:
                    # crear un nuevo record
                    record = FileRecord(**{
                        'name': f['filename'],
                        'hash': f['hash'],
                        # 'size': size,
                        'filepath': f['path'],
                        'mime': f['mime'],
                        'parent': [{
                            'id': resource_id,
                            'post_type': resource['post_type']
                        }],
                        'parents': resource['parents'],
                        'status': 'uploaded',
                        'updatedBy': current_user,
                        'updatedAt': datetime.datetime.now(),
                    })
                    # verificar que no exista un record con el mismo hash
                    record_exists = get_hash(f['hash'])

                    if not record_exists:
                        # insertar el record en la base de datos
                        new_record = mongodb.insert_record('records', record)
                        obj_resp = {
                            'id': str(new_record.inserted_id),
                            'tag': filesTags[index]['filetag']
                        }
                        if 'order' in filesTags[index]:
                            obj_resp['order'] = filesTags[index]['order']
                        resp.append(obj_resp)
                    else:
                        obj_resp = {
                            'id': str(new_record.inserted_id),
                            'tag': filesTags[index]['filetag']
                        }
                        if 'order' in filesTags[index]:
                            obj_resp['order'] = filesTags[index]['order']
                        resp.append(obj_resp)

                # registrar el log
                register_log(current_user, log_actions['record_create'], {'record': {
                    'name': record.name,
                    'hash': record.hash,
                    'size': record.size,
                    'filepath': record.filepath
                }})

                payload = record.dict()
                payload['_id'] = str(new_record.inserted_id)
                hookHandler.call('record_create', payload)
                # limpiar la cache
                
                get_hash.invalidate_all()
        else:
            raise Exception(_('File type not allowed'))

        index += 1
    # retornar el resultado
    return resp

@cacheHandler.cache.cache(limit=1000)
def get_hash(hash):
    try:
        # Buscar el recurso en la base de datos
        record = mongodb.get_record('records', {'hash': hash}, fields={'updatedBy': 0, 'updatedAt': 0})
        # Si el recurso no existe, retornar error
        if not record:
            return None
        # retornar los records
        record['_id'] = str(record['_id'])
        return record

    except Exception as e:
        raise Exception(str(e))
    
# Nuevo servicio para obtener un record por su id verificando el usuario
@cacheHandler.cache.cache(limit=5000)
def get_by_id(id, current_user, fullFields = False):
    try:
        # Buscar el record en la base de datos
        record = mongodb.get_record('records', {'_id': ObjectId(id)}, fields={'parent': 1, 'parents': 1, 'accessRights': 1, 'hash': 1, 'processing': 1, 'name': 1, 'displayName': 1, 'size': 1, 'filepath': 1})
        # Si el record no existe, retornar error
        if not record:
            return {'msg': _('Record does not exist')}, 404
        
        if 'accessRights' in record:
            if record['accessRights']:
                if not has_right(current_user, record['accessRights']) and not has_right(current_user, 'admin'):
                    return {'msg': _('You do not have permission to view this record')}, 401
        
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
            record.pop('filepath')

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
                        if not has_right(current_user, p['accessRights']['id']):
                            return {'msg': _('You do not have permission to view this record')}, 401


        if 'parents' in record:
            record.pop('parents')
            
        # Si el record existe, retornar el record
        return parse_result(record), 200

    except Exception as e:
        return {'msg': str(e)}, 500
    
def get_by_index_gallery(body, current_user):
    try:
        if 'id' not in body:
            return {'msg': _('id is missing')}, 400
        if 'index' not in body:
            return {'msg': _('index is missing')}, 400
        
        resource = mongodb.get_record('resources', {'_id': ObjectId(body['id'])}, fields={'filesObj': 1})
        ids = []
        if 'filesObj' in resource:
            for r in resource['filesObj']:
                ids.append(r['id'])

        img = list(mongodb.get_all_records('records', {'_id': {'$in': [ObjectId(id) for id in ids]}, 'processing.fileProcessing.type': 'image'}, fields={'processing': 1}, sort=[('name', 1)]).skip(body['index']).limit(1))
        return get_by_id(str(img[0]['_id']), current_user)

    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para devolver un stream de un archivo por su id
def get_stream(id, current_user):
    try:
        resp_, status = get_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        
        path, type = cache_get_record_stream(id)

        path = os.path.join(WEB_FILES_PATH, path)

        if type == 'video':
            path = path + '.mp4'
        elif type == 'audio':
            path = path + '.mp3'

        # retornar el archivo
        return send_file(path, as_attachment=False)

    except Exception as e:
        return {'msg': str(e)}, 500
    
def get_processing_metadata(id, slug, current_user):
    try:
        resp_, status = get_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        
        resp = cache_get_processing_metadata(id, slug)

        return resp, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
def get_processing_result(id, slug, current_user):
    try:
        resp_, status = get_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        
        resp = cache_get_processing_result(id, slug)

        return resp, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para devolver la transcripcion de un plugin
def get_transcription(id, slug, current_user):
    try:
        resp_, status = get_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        
        resp = cache_get_record_transcription(id, slug)
        # Si el record existe, retornar el record
        return resp, 200

    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para devolver las paginas en baja de un documento por su id
def get_document(id, current_user):
    try:
        resp_, status = get_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        return cache_get_record_document_detail(id)

    except Exception as e:
        return {'msg': str(e)}, 500

def get_document_pages(id, pages, size, current_user):
    try:
        resp_, status = get_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        pages = json.dumps(pages)
        resp = cache_get_pages_by_id(id, pages, size)
        response = Response(json.dumps(resp).encode('utf-8'), mimetype='application/json', direct_passthrough=False)
        return response
    except Exception as e:
        return {'msg': str(e)}, 500
    
def get_document_gallery(id, pages, size, current_user):
    try:
        from app.api.resources.services import get_by_id as get_resource_by_id
        resp_, status = get_resource_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        pages = json.dumps(pages)
        resp = cache_get_imgs_gallery_by_id(id, pages, size)
        response = Response(json.dumps(resp).encode('utf-8'), mimetype='application/json', direct_passthrough=False)
        return response
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500
    
def get_document_block_by_page(current_user, id, page, slug, block=None):
    try:
        resp_, status = get_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        
        print(id, page, slug, block)
        return cache_get_block_by_page_id(id, page, slug, block, current_user)
    except Exception as e:
        return {'msg': str(e)}, 500
    
def postBlockDocument(current_user, obj):
    try:
        # get record with body['id']
        record = mongodb.get_record('records', {'_id': ObjectId(obj['id_doc'])})
        # if record exists
        if record:
            # get record['processing'] and update it
            processing = record['processing']

            if obj['type_block'] == 'blocks':
                processing[obj['slug']]['result'][obj['page'] - 1]['blocks'].append({
                    'bbox': obj['bbox'],
                    **obj['data']
                })
            
            update = {
                'processing': processing,
                'updatedBy': current_user if current_user else 'system',
                'updatedAt': datetime.datetime.now()
            }

            update = FileRecordUpdate(**update)
            mongodb.update_record('records', {'_id': ObjectId(obj['id_doc'])}, update)
            
            payload = update.dict()
            payload['_id'] = obj['id_doc']
            hookHandler.call('record_update', payload)

            cache_get_block_by_page_id.invalidate_all()
            return {'msg': _('Block updated')}, 200
        else:
            return {'msg': _('Record does not exist')}, 404
    except Exception as e:
        return {'msg': str(e)}, 500

def updateBlockDocument(current_user, obj):
    try:
        # get record with body['id']
        record = mongodb.get_record('records', {'_id': ObjectId(obj['id_doc'])})
        # if record exists
        if record:
            # get record['processing'] and update it
            processing = record['processing']

            for k, val in obj['data'].items():
                if obj['type_block'] == 'blocks':
                    processing[obj['slug']]['result'][obj['page'] - 1]['blocks'][obj['index']][k] = val
            
            if obj['type_block'] == 'blocks':
                processing[obj['slug']]['result'][obj['page'] - 1]['blocks'][obj['index']]['bbox'] = obj['bbox']
            
            update = {
                'processing': processing,
                'updatedBy': current_user if current_user else 'system',
                'updatedAt': datetime.datetime.now()
            }

            update = FileRecordUpdate(**update)
            mongodb.update_record('records', {'_id': ObjectId(obj['id_doc'])}, update)
            
            payload = update.dict()
            payload['_id'] = obj['id_doc']
            hookHandler.call('record_update', payload)

            cache_get_block_by_page_id.invalidate_all()
            return {'msg': _('Block updated')}, 200
        else:
            return {'msg': _('Record does not exist')}, 404
    except Exception as e:
        return {'msg': str(e)}, 500
    
def deleteBlockDocument(current_user, obj):
    try:
        # get record with body['id']
        record = mongodb.get_record('records', {'_id': ObjectId(obj['id_doc'])})
        # if record exists
        if record:
            # get record['processing'] and update it
            processing = record['processing']

            if obj['type_block'] == 'blocks':
                processing[obj['slug']]['result'][obj['page'] - 1]['blocks'].pop(obj['index'])
            
            update = {
                'processing': processing,
                'updatedBy': current_user if current_user else 'system',
                'updatedAt': datetime.datetime.now()
            }

            update = FileRecordUpdate(**update)
            mongodb.update_record('records', {'_id': ObjectId(obj['id_doc'])}, update)
            
            payload = update.dict()
            payload['_id'] = obj['id_doc']
            hookHandler.call('record_update', payload)

            cache_get_block_by_page_id.invalidate_all()
            return {'msg': _('Block deleted')}, 200
        else:
            return {'msg': _('Record does not exist')}, 404
    except Exception as e:
        return {'msg': str(e)}, 500
    
@cacheHandler.cache.cache(limit=2000)
def get_favCount(id):
    try:
        record = mongodb.get_record('records', {'_id': ObjectId(id)}, fields={'favCount': 1})
        if not record:
            return {'msg': _('Record does not exist')}, 404
        return record['favCount']
    except Exception as e:
        raise Exception(str(e))
    
def add_to_favCount(id):
    try:
        update = {
            '$inc': {
                'favCount': 1
            },
            'updatedBy': 'system',
            'updatedAt': datetime.datetime.now()
        }
        update_ = FileRecordUpdate(**update)
        mongodb.update_record('records', {'_id': ObjectId(id)}, update_)
        get_favCount.invalidate(id)
    except Exception as e:
        raise Exception(str(e))
    
def remove_from_favCount(id):
    try:
        update = {
            '$inc': {
                'favCount': -1
            },
            'updatedBy': 'system',
            'updatedAt': datetime.datetime.now()
        }
        update_ = FileRecordUpdate(**update)
        mongodb.update_record('records', {'_id': ObjectId(id)}, update_)
        get_favCount.invalidate(id)
    except Exception as e:
        raise Exception(str(e))
    
def generate_text_transcription(segments):
    pattern = r'\s*(transcribed by.*|subtitles by.*|by.*\.com|by.*\.org|http.*|.com*)$'
    text = ''
    current_speaker = ''
    
    for segment in segments:
        if re.search(pattern, segment['text']):
            continue
        if 'speaker' in segment and segment['speaker']:
            if current_speaker != segment['speaker']:
                current_speaker = segment['speaker']
                text += '\n\n' + current_speaker + ': ' + segment['text'] + ' '
            else:
                text += segment['text'] + ' '
        else:
            text += segment['text'] + ' '
    return text
    
def is_transcriber_can_edit(recordId, user):
    if has_role(user, 'transcriber'):
        task = mongodb.get_record('usertasks', {'recordId': recordId, 'user': user, 'status': {'$in': ['review', 'pending', 'rejected']}}, fields={'_id': 1})
        if task:
            return True
        else:
            return False
    return None 
    
def delete_transcription_segment(id, body, user):
    resp_, status = get_by_id(id, user)
    if status != 200:
        return {'msg': resp_['msg']}, 500
    
    can_edit = is_transcriber_can_edit(id, user)
    if can_edit is False:
        return {'msg': _('You do not have permission to edit this transcription')}, 401
    
    slug = body['slug']
    index = body['index']

    record = mongodb.get_record('records', {'_id': ObjectId(id)}, fields={'processing': 1})
    if not record:
        return {'msg': _('Record does not exist')}, 404
    if 'processing' not in record:
        return {'msg': _('Record does not have transcription')}, 404
    if slug not in record['processing']:
        return {'msg': _('Record does not have transcription')}, 404
    if record['processing'][slug]['type'] != 'av_transcribe':
        return {'msg': _(u'Record has not been processed with {slug}', slug=slug)}, 404
    
    segments = record['processing'][slug]['result']['segments']
    

    segments.pop(index)

    update = {
        'processing': record['processing'],
        'updatedBy': user if user else 'system',
        'updatedAt': datetime.datetime.now()
    }

    update['processing'][slug]['result']['segments'] = segments
    update['processing'][slug]['result']['text'] = generate_text_transcription(segments)

    update = FileRecordUpdate(**update)
    mongodb.update_record('records', {'_id': ObjectId(id)}, update)
    
    payload = update.dict()
    payload['_id'] = id
    hookHandler.call('record_update', payload)

    cache_get_record_transcription.invalidate(id, slug)
    cache_get_record_transcription.invalidate(id, slug, False)

    return {'msg': _('Transcription segment deleted')}, 200

def edit_transcription_speaker(id, body, user):
    resp_, status = get_by_id(id, user)
    if status != 200:
        return {'msg': resp_['msg']}, 500
    
    can_edit = is_transcriber_can_edit(id, user)
    if can_edit is False:
        return {'msg': _('You do not have permission to edit this transcription')}, 401
    
    slug = body['slug']
    
    record = mongodb.get_record('records', {'_id': ObjectId(id)}, fields={'processing': 1})
    if not record:
        return {'msg': _('Record does not exist')}, 404
    if 'processing' not in record:
        return {'msg': _('Record does not have transcription')}, 404
    if slug not in record['processing']:
        return {'msg': _('Record does not have transcription')}, 404
    if record['processing'][slug]['type'] != 'av_transcribe':
        return {'msg': _(u'Record has not been processed with {slug}', slug=slug)}, 404
    
    segments = record['processing'][slug]['result']['segments']
    updateSpeaker = False
    
    if 'speaker' in body and 'oldSpeaker' in body:
        updateSpeaker = body['speaker']
        oldSpeaker = body['oldSpeaker']
        
    if updateSpeaker:
        for segment in segments:
            if 'speaker' in segment:
                if segment['speaker'] == oldSpeaker:
                    segment['speaker'] = updateSpeaker
                    
    update = {
        'processing': record['processing'],
        'updatedBy': user if user else 'system',
        'updatedAt': datetime.datetime.now()
    }

    update['processing'][slug]['result']['segments'] = segments
    update['processing'][slug]['result']['text'] = generate_text_transcription(segments)

    update = FileRecordUpdate(**update)
    mongodb.update_record('records', {'_id': ObjectId(id)}, update)
    
    payload = update.dict()
    payload['_id'] = id
    hookHandler.call('record_update', payload)

    cache_get_record_transcription.invalidate(id, slug)
    cache_get_record_transcription.invalidate(id, slug, False)
    
    return {'msg': _('Transcription speaker edited')}, 200
    
def edit_transcription(id, body, user):
    resp_, status = get_by_id(id, user)
    if status != 200:
        return {'msg': resp_['msg']}, 500
    
    can_edit = is_transcriber_can_edit(id, user)
    if can_edit is False:
        return {'msg': _('You do not have permission to edit this transcription')}, 401
    
    slug = body['slug']

    record = mongodb.get_record('records', {'_id': ObjectId(id)}, fields={'processing': 1})
    if not record:
        return {'msg': _('Record does not exist')}, 404
    if 'processing' not in record:
        return {'msg': _('Record does not have transcription')}, 404
    if slug not in record['processing']:
        return {'msg': _('Record does not have transcription')}, 404
    if record['processing'][slug]['type'] != 'av_transcribe':
        return {'msg': _(u'Record has not been processed with {slug}', slug=slug)}, 404
    
    segments = record['processing'][slug]['result']['segments']

    segments[body['index']]['text'] = body['text']
    segments[body['index']]['start'] = body['start']
    segments[body['index']]['end'] = body['end']
    if 'speaker' in body:
        segments[body['index']]['speaker'] = body['speaker']

    update = {
        'processing': record['processing'],
        'updatedBy': user if user else 'system',
        'updatedAt': datetime.datetime.now()
    }

    update['processing'][slug]['result']['segments'] = segments
    update['processing'][slug]['result']['text'] = generate_text_transcription(segments)

    update = FileRecordUpdate(**update)
    mongodb.update_record('records', {'_id': ObjectId(id)}, update)
    
    payload = update.dict()
    payload['_id'] = id
    hookHandler.call('record_update', payload)

    cache_get_record_transcription.invalidate(id, slug)
    cache_get_record_transcription.invalidate(id, slug, False)

    return {'msg': _('Transcription segment edited')}, 200

def download_records(body, user):
    try:
        from app.api.system.services import get_system_settings
        settings, status = get_system_settings()
        capabilities = settings['capabilities']
        
        if 'files_download' not in capabilities:
            return {'msg': _('Files download isn\'t active')}, 400
        
        if 'id' not in body:
            return {'msg': _('id is missing')}, 400
        
        resp_, status = get_by_id(body['id'], user, True)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        
        record = resp_
        if 'processing' not in record:
            return {'msg': _('Record does not have processing')}, 404
        
        if 'fileProcessing' not in record['processing']:
            return {'msg': _('Record does not have fileProcessing')}, 404
        
        if 'type' not in record['processing']['fileProcessing']:
            return {'msg': _('Record does not have fileProcessing type')}, 404
        
        if 'accessRights' in record:
            if record['accessRights']:
                if not has_right(user, record['accessRights']) and not has_role(user, 'admin'):
                    return {'msg': _('You do not have permission to view this record')}, 401
        
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