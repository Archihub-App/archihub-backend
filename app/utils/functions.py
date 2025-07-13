from app.utils import DatabaseHandler
from app.utils import CacheHandler
from functools import lru_cache
from bson import json_util
import json
from bson.objectid import ObjectId
import os
from dotenv import load_dotenv
from PIL import Image
from flask import Response, jsonify
import datetime
import base64
load_dotenv()

WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

def clear_cache():
    cache_type_roles.invalidate_all()
    get_resource_records.invalidate_all()
    get_roles.invalidate_all()
    get_roles_id.invalidate_all()
    get_access_rights_id.invalidate_all()
    get_access_rights.invalidate_all()
    get_resource_records.invalidate_all()
    cache_get_record_stream.invalidate_all()
    cache_get_record_transcription.invalidate_all()
    cache_get_record_document_detail.invalidate_all()
    cache_get_block_by_page_id.invalidate_all()
    cache_get_pages_by_id.invalidate_all()
    cache_get_imgs_gallery_by_id.invalidate_all()
    cache_type_roles.invalidate_all()
    cache_get_processing_metadata.invalidate_all()
    cache_get_processing_result.invalidate_all()
    has_right.invalidate_all()
    has_role.invalidate_all()

@cacheHandler.cache.cache()
def get_roles_id():
    try:
        # Obtener el registro access_rights de la colección system
        access_rights = mongodb.get_record('system', {'name': 'access_rights'})
        # Si el registro no existe, retornar error
        if not access_rights:
            raise Exception('No existe el registro access_rights')

        roles = access_rights['data'][1]['value']

        return roles
    except Exception as e:
        return None


@cacheHandler.cache.cache()
def get_roles():
    try:
        id_roles = get_roles_id()
        if id_roles:
            # Obtener el listado con roles
            list = get_list_by_id(get_roles_id())

            temp = [*list['options']]
        else:
            temp = []
        # Agregar admin y editor a la lista
        temp.append({'id': 'user', 'term': 'user'})
        temp.append({'id': 'admin', 'term': 'admin'})
        temp.append({'id': 'editor', 'term': 'editor'})
        temp.append({'id': 'publisher', 'term': 'publisher'})
        temp.append({'id': 'visualizer', 'term': 'visualizer'})
        temp.append({'id': 'processing', 'term': 'processing'})
        temp.append({'id': 'team_lead', 'term': 'team_lead'}) # rol que se usa para asignar tareas a un equipo
        temp.append({'id': 'transcriber', 'term': 'transcriber'}) # rol que se usa para permitir a un usuario transcribir audios y videos
        temp.append({'id': 'llm', 'term': 'llm'}) # rol que se usa para permitir a un usuario usar LLMs

        return {
            'options': temp
        }

    except Exception as e:
        raise Exception(
            'Error al obtener el registro access_rights: ' + str(e))


@cacheHandler.cache.cache()
def get_access_rights_id():
    try:
        # Obtener el registro access_rights de la colección system
        access_rights = mongodb.get_record('system', {'name': 'access_rights'})
        # Si el registro no existe, retornar error
        if not access_rights:
            raise Exception('No existe el registro access_rights')

        list_id = access_rights['data'][0]['value']

        return list_id

    except Exception as e:
        return None


@cacheHandler.cache.cache()
def get_access_rights():
    try:
        access_id = get_access_rights_id()
        if access_id:
            # Obtener el listado con access_rights
            list = get_list_by_id(get_access_rights_id())

            temp = [*list['options']]
        else:

            list = []
        # Obtener el listado con list_id
        return list

    except Exception as e:
        raise Exception('Error al obtener el registro access_rights')


def verify_role_exists(compare):
    roles = get_roles()['options']
    temp = []

    for role in compare:
        if role['id'] not in [r['id'] for r in roles]:
            raise Exception('El rol ' + role['id'] + ' no existe')
        temp.append(role['id'])

    return temp


def verify_accessright_exists(compare):
    access_rights = get_access_rights()['options']
    temp = []

    for access_right in compare:
        if access_right['id'] not in [r['id'] for r in access_rights]:
            raise Exception('El derecho de acceso ' +
                            access_right['id'] + ' no existe')
        temp.append(access_right['id'])

    return temp


def get_list_by_id(id):
    try:
        # Buscar el listado en la base de datos
        lista = mongodb.get_record('lists', {'_id': ObjectId(id)})
        # a lista solo le dejamos los campos name, description, y options
        lista = {
            'name': lista['name'], 'description': lista['description'], 'options': lista['options']}
        # Si el listado no existe, retornar error
        if not lista:
            return {'msg': 'Listado no existe'}

        opts = []

        records = mongodb.get_all_records('options', {
                                          '_id': {'$in': [ObjectId(id) for id in lista['options']]}}, [('term', 1)])

        # opts es igual a un arreglo de diccionarios con los campos id y term
        for record in records:
            opts.append({'id': str(record['_id']), 'term': record['term']})

        # agregamos los campos al listado
        lista['options'] = opts
        # Parsear el resultado
        lista = parse_result(lista)

        # Retornar el resultado
        return lista
    except Exception as e:
        return {'msg': str(e)}, 500


def parse_result(result):
    return json.loads(json_util.dumps(result))


@cacheHandler.cache.cache(limit=500)
def get_resource_records(ids, user, page=0, limit=10, groupImages=False):
    ids = json.loads(ids)
    ids_filter = []
    for i in range(len(ids)):
        ids_filter.append(ObjectId(ids[i]['id']))

    try:
        filters = {
            '_id': {'$in': ids_filter},
        }
        if groupImages:
            filters['$or'] = [{'processing.fileProcessing.type': {'$exists': False}}, {'processing.fileProcessing.type': {'$ne': 'image'}}]

        cursor = mongodb.get_all_records('records', filters=filters, fields={
                  'name': 1, 'size': 1, 'accessRights': 1, 'displayName': 1, 'processing': 1, 'hash': 1, 'filepath': 1})
        
        if limit is not None:
            cursor = cursor.skip(page * limit).limit(limit)
            
        r_ = list(cursor)
        
        for r in r_:
            r['_id'] = str(r['_id'])
            r['tag'] = [x['tag'] for x in ids if x['id'] == r['_id']][0]

            if 'accessRights' in r:
                if r['accessRights']:
                    if not has_right(user, r['accessRights']) and not has_role(user, 'admin'):
                        r['name'] = 'No tiene permisos para ver este archivo'
                        r['displayName'] = 'No tiene permisos para ver este archivo'
                        r['_id'] = None
                        r.pop('filepath', None)

            pro_dict = {}
            if 'processing' in r:
                if 'fileProcessing' in r['processing']:
                    pro_dict['fileProcessing'] = {
                        'type': r['processing']['fileProcessing']['type'],
                    }
                    
            if limit is not None:
                r.pop('filepath', None)
                r['processing'] = pro_dict

        if groupImages:
            img = mongodb.count('records', {'_id': {'$in': ids_filter}, 'processing.fileProcessing.type': 'image'})
            
            if img > 0:
                r_.append({
                    '_id': 'imgGallery',
                    'displayName': f'{str(img)} imágenes',
                    'hash': '',
                    'id': 'imgGallery',
                    'processing': {
                        'fileProcessing': {
                            'type': 'image gallery'
                        }
                    },
                    'tag': 'Galería de imágenes',
                })

        return r_

    except Exception as e:
        raise Exception(str(e))

@cacheHandler.cache.cache(limit=500)
def get_resource_records_public(ids, page=0, limit=10, groupImages=False):
    ids = json.loads(ids)
    ids_filter = []
    for i in range(len(ids)):
        ids_filter.append(ObjectId(ids[i]['id']))

    try:
        filters = {
            '_id': {'$in': ids_filter},
        }
        if groupImages:
            filters['$or'] = [{'processing.fileProcessing.type': {'$exists': False}}, {'processing.fileProcessing.type': {'$ne': 'image'}}]
            
        cursor = mongodb.get_all_records('records', filters=filters, fields={
                  'name': 1, 'size': 1, 'accessRights': 1, 'displayName': 1, 'processing': 1, 'hash': 1, 'filepath': 1})
        
        if limit is not None:
            cursor = cursor.skip(page * limit).limit(limit)
        
        r_ = list(cursor)
        
        for r in r_:
            r['_id'] = str(r['_id'])
            r['tag'] = [x['tag'] for x in ids if x['id'] == r['_id']][0]

            if 'accessRights' in r:
                if r['accessRights']:
                    r['name'] = 'No tiene permisos para ver este archivo'
                    r['displayName'] = 'No tiene permisos para ver este archivo'
                    r['_id'] = None
                    r.pop('filepath', None)

            pro_dict = {}
            if 'processing' in r:
                if 'fileProcessing' in r['processing']:
                    pro_dict['fileProcessing'] = {
                        'type': r['processing']['fileProcessing']['type'],
                    }

            if limit is not None:
                r.pop('filepath', None)
                r['processing'] = pro_dict

        if groupImages:
            img = mongodb.count('records', {'_id': {'$in': ids_filter}, 'processing.fileProcessing.type': 'image'})
            
            if img > 0:
                r_.append({
                    '_id': 'imgGallery',
                    'displayName': f'{str(img)} imágenes',
                    'hash': '',
                    'id': 'imgGallery',
                    'processing': {
                        'fileProcessing': {
                            'type': 'image gallery'
                        }
                    },
                    'tag': 'Galería de imágenes',
                })

        return r_

    except Exception as e:
        raise Exception(str(e))


@cacheHandler.cache.cache(limit=1000)
def cache_get_record_stream(id):
    # Buscar el record en la base de datos
    record = mongodb.get_record('records', {'_id': ObjectId(id)}, fields={
                                'filepath': 1, 'processing': 1})

    # Si el record no existe, retornar error
    if not record:
        raise Exception('Record no existe')
    # si el record no se ha procesado, retornar error
    if 'processing' not in record:
        if 'fileProcessing' not in record['processing']:
            raise Exception('Record no ha sido procesado')

    # si el record no es de tipo audio o video, retornar error
    if record['processing']['fileProcessing']['type'] != 'audio' and record['processing']['fileProcessing']['type'] != 'video':
        raise Exception('Record no es de tipo audio o video')

    # obtener el path del archivo
    path = record['processing']['fileProcessing']['path']
    type = record['processing']['fileProcessing']['type']
    
    return path, type

@cacheHandler.cache.cache(limit=1000)
def cache_get_processing_result(id, slug):
    # Buscar el record en la base de datos
    record = mongodb.get_record(
        'records', {'_id': ObjectId(id)}, fields={'processing': 1})

    # Si el record no existe, retornar error
    if not record:
        raise Exception('Record no existe')
    # si el record no se ha procesado, retornar error
    if 'processing' not in record:
        raise Exception('Record no ha sido procesado')
    if slug not in record['processing']:
        raise Exception('Record no ha sido procesado con el slug ' + slug)

    result = record['processing'][slug]['result']
    # iterate each key in result and if the value is a datetime, convert it to string
    for key in result:
        if isinstance(result[key], datetime.datetime):
            result[key] = result[key].strftime('%Y-%m-%d %H:%M:%S')
        elif isinstance(result[key], list):
            for i in range(len(result[key])):
                if isinstance(result[key][i], datetime.datetime):
                    result[key][i] = result[key][i].strftime('%Y-%m-%d %H:%M:%S')

    return result

@cacheHandler.cache.cache(limit=1000)
def cache_get_processing_metadata(id, slug):
    # Buscar el record en la base de datos
    record = mongodb.get_record(
        'records', {'_id': ObjectId(id)}, fields={'processing': 1})

    # Si el record no existe, retornar error
    if not record:
        raise Exception('Record no existe')
    # si el record no se ha procesado, retornar error
    if 'processing' not in record:
        raise Exception('Record no ha sido procesado')
    if slug not in record['processing']:
        raise Exception('Record no ha sido procesado con el slug ' + slug)
    if 'metadata' not in record['processing'][slug]['result']:
        raise Exception('Record no tiene metadata')

    return record['processing'][slug]['result']['metadata']

@cacheHandler.cache.cache(limit=1000)
def cache_get_record_transcription(id, slug, segments=True):
    # Buscar el record en la base de datos
    record = mongodb.get_record(
        'records', {'_id': ObjectId(id)}, fields={'processing': 1})

    # Si el record no existe, retornar error
    if not record:
        raise Exception('Record no existe')
    # si el record no se ha procesado, retornar error
    if slug not in record['processing']:
        raise Exception('Record no ha sido procesado')
    if record['processing'][slug]['type'] != 'av_transcribe':
        raise Exception('Record no ha sido procesado con el slug ' + slug)

    resp = {
        'segments': record['processing'][slug]['result']['segments']
    }

    temp = []
    hasSpeakers = False

    for s in resp['segments']:
        speaker = s['speaker'] if 'speaker' in s else None

        if speaker:
            hasSpeakers = True

        obj = {
            'text': s['text'],
            'start': s['start'],
            'end': s['end'],
            'speaker': speaker
        }

        temp.append(obj)

    speakers = None
    if hasSpeakers:
        speakers = []
        for s in temp:
            if s['speaker']:
                if s['speaker'] not in [sp['name'] for sp in speakers]:
                    speakers.append({'name': s['speaker'], 'segments': [
                        {'start': s['start'], 'end': s['end']}
                    ]})
                else:
                    for sp in speakers:
                        if sp['name'] == s['speaker']:
                            if s['start'] - sp['segments'][-1]['end'] < 5:
                                sp['segments'][-1]['end'] = s['end']
                            else:
                                sp['segments'].append({'start': s['start'], 'end': s['end']})
        
        for sp in speakers:
            total = 0
            for seg in sp['segments']:
                total += seg['end'] - seg['start']
            sp['total'] = total
                
    transcription = {
        'text': record['processing'][slug]['result']['text'],
    }
    
    if segments:
        transcription = {
            'segments': temp,
            'speakers': speakers
        }

    return transcription


@cacheHandler.cache.cache(limit=1000)
def cache_get_record_document_detail(id):
    # Buscar el record en la base de datos
    record = mongodb.get_record(
        'records', {'_id': ObjectId(id)}, fields={'processing': 1})

    # Si el record no existe, retornar error
    if not record:
        raise Exception('Record no existe')
    # si el record no se ha procesado, retornar error
    if 'processing' not in record:
        raise Exception('Record no ha sido procesado')
    if 'fileProcessing' not in record['processing']:
        raise Exception('Record no ha sido procesado')
    

    if record['processing']['fileProcessing']['type'] == 'document':
        path = record['processing']['fileProcessing']['path']
        path_small = os.path.join(WEB_FILES_PATH, path, 'web/small/')

        files = sorted(os.listdir(path_small))
        if len(files) == 0:
            raise Exception('Record no tiene archivos')
        
        # get the first file in the directory and get the dimensions of the image
        file = files[0]
        file = os.path.join(path_small, file)
        img = Image.open(file)
        width, height = img.size
        aspect_ratio = width / height
        
        return {
            'pages': len(files),
            'aspect_ratio': aspect_ratio
        }
    elif record['processing']['fileProcessing']['type'] == 'image':
        path = record['processing']['fileProcessing']['path']
        path_small = os.path.join(WEB_FILES_PATH, path)
        path_small = path_small + '_small.jpg'

        if not os.path.exists(path_small):
            raise Exception('Record no tiene archivos')
        
        img = Image.open(path_small)
        width, height = img.size
        aspect_ratio = width / height
        
        return {
            'pages': 1,
            'aspect_ratio': aspect_ratio
        }
    
@cacheHandler.cache.cache(limit=1000)
def cache_get_block_by_page_id(id, page, slug, block=None, user=None):
    record = mongodb.get_record(
        'records', {'_id': ObjectId(id)}, fields={'processing': 1})
    
    # Si el record no existe, retornar error
    if not record:
        from app.api.records.services import get_by_index_gallery
        record, status = get_by_index_gallery({
            'id': id,
            'index': page
        }, user)

        if status != 200:
            raise Exception(record['msg'])
        
        record = mongodb.get_record(
        'records', {'_id': ObjectId(record['_id']['$oid'])}, fields={'processing': 1})
        
    # si el record no se ha procesado, retornar error
    if 'processing' not in record:
        raise Exception('Record no ha sido procesado')
    if 'fileProcessing' not in record['processing']:
        raise Exception('Record no ha sido procesado')
    
    # get path of the file and calculate aspect ratio
    if record['processing']['fileProcessing']['type'] == 'document':
        path = record['processing']['fileProcessing']['path']
        path_files = os.path.join(WEB_FILES_PATH, path, 'web/big/')
        path = os.path.join(WEB_FILES_PATH, path)

        files = sorted(os.listdir(path_files))

        if page > len(files):
            raise Exception('Record no tiene tantas páginas')
        
        # verificar si el archivo existe
        file = files[page - 1]
        file = os.path.join(path_files, file)
        if not os.path.exists(file):
            raise Exception('No existe el archivo')

        resp = record['processing'][slug]['result'][page - 1]
        labels = record['processing'][slug]['labels']

        if block == 'blocks':
            resp['labels'] = labels
            for b in resp['blocks']:
                if 'words' in b:
                    del b['words']
        elif block == 'words':
            resp_ = {
                'page': page,
                'words': [],
                'labels': labels
            }
            for b in resp['blocks']:
                if 'words' in b:
                    resp_['words'] += b['words']

            resp = resp_
        else:
            return {'msg': 'Record no tiene bloques o palabras'}, 400
    
        return resp, 200
    elif record['processing']['fileProcessing']['type'] == 'image':
        resp = record['processing'][slug]['result']

        if 'blocks' in resp:
            return resp['blocks'], 200
        else:
            return {'msg': 'Record no tiene bloques'}, 400
    else:
        return {'msg': 'Record no es de tipo document'}, 400    

@cacheHandler.cache.cache(limit=5000)
def cache_get_imgs_gallery_by_id(id, pages, size):
    pages = json.loads(pages)

    if len(pages) == 0:
        return []
    
    resource = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'filesObj': 1})
    ids = []
    if 'filesObj' in resource:
        for r in resource['filesObj']:
            ids.append(r['id'])

    img = list(mongodb.get_all_records('records', {'_id': {'$in': [ObjectId(id) for id in ids]}, 'processing.fileProcessing.type': 'image'}, fields={'processing': 1}))
    
    order_dict = {file['id']: file['order'] if 'order' in file else 0 for file in resource['filesObj']}

    img_sorted = sorted(img, key=lambda x: order_dict.get(x['_id'], float('inf')))

    img = img_sorted
    
    img = img[pages[0]:pages[0] + len(pages)]
    
    response = []
    
    for i in range(len(img)):
        path = img[i]['processing']['fileProcessing']['path']
        path_img = os.path.join(WEB_FILES_PATH, path)
        if size == 'big': size = 'large'
        path_img = path_img + '_' + size + '.jpg'

        if not os.path.exists(path_img):
            raise Exception('No existe el archivo')
        
        img_ = Image.open(path_img)
        width, height = img_.size
        aspect_ratio = width / height

        with open(path_img, 'rb') as f:
            data = f.read()
            encoded_data = base64.b64encode(data).decode('utf-8')
            response.append({'filename': os.path.basename(path_img), 'data': encoded_data, 'aspect_ratio': aspect_ratio})

    return response
        

@cacheHandler.cache.cache(limit=5000)
def cache_get_pages_by_id(id, pages, size):
    pages = json.loads(pages)
    
    if len(pages) == 0:
        return []
    # Buscar el record en la base de datos
    record = mongodb.get_record(
        'records', {'_id': ObjectId(id)}, fields={'processing': 1})

    # Si el record no existe, retornar error
    if not record:
        raise Exception('Record no existe')
    # si el record no se ha procesado, retornar error
    if 'processing' not in record:
        raise Exception('Record no ha sido procesado')
    if 'fileProcessing' not in record['processing']:
        raise Exception('Record no ha sido procesado')
    
    
    if record['processing']['fileProcessing']['type'] == 'document':
        path = record['processing']['fileProcessing']['path']
        path_files = os.path.join(WEB_FILES_PATH, path, 'web/' + size + '/')
        path = os.path.join(WEB_FILES_PATH, path)

        files = sorted(os.listdir(path_files))

        response = []
        for x in pages:
            if x >= len(files):
                raise Exception('Record no tiene tantas páginas')
            
            # verificar si el archivo existe
            file = files[x]
            file = os.path.join(path_files, file)
            if not os.path.exists(file):
                raise Exception('No existe el archivo')
            
            with open(file, 'rb') as f:
                data = f.read()
                encoded_data = base64.b64encode(data).decode('utf-8')
                response.append({'filename': os.path.basename(file), 'data': encoded_data})
            
        return response
    
    elif record['processing']['fileProcessing']['type'] == 'image':
        path = record['processing']['fileProcessing']['path']
        path_img = os.path.join(WEB_FILES_PATH, path)
        if size == 'big': size = 'large'
        path_img = path_img + '_' + size + '.jpg'

        if not os.path.exists(path_img):
            raise Exception('No existe el archivo')
        
        response = {}
        img = Image.open(path_img)
        width, height = img.size
        aspect_ratio = width / height

        with open(path_img, 'rb') as f:
            data = f.read()
            encoded_data = base64.b64encode(data).decode('utf-8')
            response = [{'filename': os.path.basename(path_img), 'data': encoded_data, 'aspect_ratio': aspect_ratio}]

        return response
        
@cacheHandler.cache.cache()
def cache_type_roles(slug):
    try:
        # Obtener el tipo de contenido por su slug
        type = mongodb.get_record('post_types', {'slug': slug}, fields={'editRoles': 1, 'viewRoles': 1})

        # Si el tipo de contenido no existe, retornar error
        if not type:
            raise Exception('Tipo de contenido no existe')
        
        roles = {
            'editRoles': None,
            'viewRoles': None
        }

        if 'editRoles' in type:
            if len(type['editRoles']) > 0:
                roles['editRoles'] = type['editRoles']

        if 'viewRoles' in type:
            if len(type['viewRoles']) > 0:
                roles['viewRoles'] = type['viewRoles']

        return roles
    except Exception as e:
        raise Exception(
            'Error al obtener el registro access_rights: ' + str(e))
    
@cacheHandler.cache.cache()
def has_right(username, right):
    user = mongodb.get_record('users', {'username': username})
    # Si el usuario no existe, retornar error
    if not user:
        return jsonify({'msg': 'Usuario no existe'}), 400
    # Si el usuario tiene el rol, retornar True
    if right in user['accessRights']:
        return True
    # Si el usuario no tiene el rol, retornar False
    return False

@cacheHandler.cache.cache()
def has_role(username, role):
    user = mongodb.get_record('users', {'username': username})
    # Si el usuario no existe, retornar error
    if not user:
        return jsonify({'msg': 'Usuario no existe'}), 400
    # Si el usuario tiene el rol, retornar True
    if role in user['roles']:
        return True
    # Si el usuario no tiene el rol, retornar False
    return False

def find_by_id(data_array, id_value):
    for item in data_array:
        if item.get('id') == id_value:
            return item
    return None