import datetime
from flask import jsonify, request
from app.utils import DatabaseHandler
from bson import json_util
import json
from functools import lru_cache
from bson.objectid import ObjectId
from app.api.records.models import Record as FileRecord
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.api.records.models import RecordUpdate as FileRecordUpdate
from werkzeug.utils import secure_filename
import os
import hashlib
import magic
import uuid

# get path of root folder

UPLOAD_FOLDER = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', '..', '..'))
# Make a folder for uploads if it doesn't exist
if not os.path.exists(os.path.join(UPLOAD_FOLDER, 'uploads')):
    os.makedirs(os.path.join(UPLOAD_FOLDER, 'uploads'))

UPLOAD_FOLDER = os.path.join(UPLOAD_FOLDER, 'uploads')

ALLOWED_EXTENSIONS = set(['txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif',
                          'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'csv', 'zip', 'rar', 'mp4',
                          'mp3', 'wav', 'avi', 'mkv', 'flv', 'mov', 'wmv'])

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para parsear el resultado de una consulta a la base de datos


def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para obtener todos los records para un recurso


@lru_cache(maxsize=1000)
def get_all(resource_id, current_user):
    try:
        # Buscar el recurso en la base de datos
        record = mongodb.get_record('records', {'parents.id': resource_id})
        # Si el recurso no existe, retornar error
        if not record:
            return {'msg': 'Recurso no existe'}, 404
        # retornar los records
        return jsonify(record['records']), 200

    except Exception as e:
        return {'msg': str(e)}, 500


def allowedFile(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_by_filters(body, current_user):
    try:
        # Buscar el recurso en la base de datos
        records = list(mongodb.get_all_records(
            'records', body['filters'], limit=20, skip=body['page'] * 20))
        # Si el recurso no existe, retornar error
        if not records:
            return {'msg': 'Recurso no existe'}, 404
        
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
@lru_cache(maxsize=500)
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
def update_record(record, current_user):
    try:
        update = {
            'displayName': record['displayName'],
            'accessRights': record['accessRights']
        }

        mongodb.update_record('records', {'_id': ObjectId(record['id'])}, update)
    except Exception as e:
        raise Exception(str(e))

# Nuevo servicio para borrar un parent de un record
def delete_parent(resource_id, parent_id, current_user):
    try:
        # Buscar el record en la base de datos
        record = mongodb.get_record('records', {'_id': ObjectId(parent_id)})

        # Si el record no existe, retornar error
        if not record:
            return {'msg': 'Record no existe'}, 404

        # Si el record no tiene el recurso como parent, retornar error
        if not any(x['id'] == resource_id for x in record['parent']):
            return {'msg': 'Record no tiene el recurso como parent'}, 404

        # Si el record tiene el recurso como parent, eliminarlo
        # el parent es de tipo dict y tiene los campos id y post_type
        record['parent'] = [x for x in record['parent']
                            if x['id'] != resource_id]
        
        array_parents = set(x['id'] for x in record['parent'])

        array_parents_temp = []
        # iterar sobre parent y en un nuevo array ir guardando los padres de cada parent
        for p in array_parents:
            r = mongodb.get_record('resources', {'_id': ObjectId(p)})

            if r:
                # se agregan los parents a array_parents si no estan ya en el array. Cada parent en el array_parents es del tipo {id: id, post_type: post_type}
                for parent in r['parents']:
                    print(parent)
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
            'parent': record['parent'],
            'parents': array_parents,
            'status': status
        })

        mongodb.update_record('records', {'_id': ObjectId(parent_id)}, update)

        # Registrar el log
        register_log(current_user, log_actions['record_update'], {
                     'record': parent_id})
        # Limpiar la cache
        get_all.cache_clear()

        # Retornar el resultado
        return {'msg': 'Parent eliminado exitosamente'}, 200

    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500


def update_parent(parent_id, current_user, parents):
    unique_array_parents = set(x['id'] for x in parents)

    new_list = [next(item for item in parents if item['id'] == id)
                for id in unique_array_parents]

    update = FileRecordUpdate(**{
        'parents': new_list
    })

    mongodb.update_record('records', {'_id': ObjectId(parent_id)}, update)

    # Registrar el log
    register_log(current_user, log_actions['record_update'], {
        'record': parent_id})
    # Limpiar la cache
    get_all.cache_clear()


# Nuevo servicio para crear un record para un recurso
def create(resource_id, current_user, files):
    try:
        # Buscar el recurso en la base de datos
        resource = mongodb.get_record(
            'resources', {'_id': ObjectId(resource_id)})
        # Si el recurso no existe, retornar error
        if not resource:
            return {'msg': 'Recurso no existe'}, 404

        resp = []

        for f in files:
            filename = secure_filename(f.filename)
            if allowedFile(filename):
                # generar un nombre unico para el archivo
                filename_new = str(uuid.uuid4()) + '.' + \
                    filename.rsplit('.', 1)[1].lower()
                # coger la fecha actual y convertirla a string de la forma YYYY/MM/DD
                date = datetime.datetime.now().strftime("%Y/%m/%d")
                # hacer un path en base a la fecha actual
                path = os.path.join(UPLOAD_FOLDER, date)
                # crear el directorio para guardar el archivo usando la ruta date
                if not os.path.exists(path):
                    os.makedirs(path)
                f.save(os.path.join(path, filename))
                # renombrar el archivo
                os.rename(os.path.join(path, filename),
                          os.path.join(path, filename_new))
                # calcular el hash 256 del archivo
                hash = hashlib.sha256()
                with open(os.path.join(path, filename_new), 'rb') as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hash.update(chunk)

                # se verifica si el hash del archivo ya existe en la base de datos
                record = mongodb.get_record(
                    'records', {'hash': hash.hexdigest()})
                # si el record existe, se agrega el recurso como padre
                if record:
                    # eliminar el archivo que se subio
                    os.remove(os.path.join(path, filename_new))

                    resp.append(str(record['_id']))

                    new_parent = [{
                        'id': resource_id,
                        'post_type': resource['post_type']
                    }, *record['parent']]

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

                    # actualizar el record
                    update = FileRecordUpdate(**update_dict)
                    mongodb.update_record(
                        'records', {'_id': record['_id']}, update)

                    # registrar el log
                    register_log(current_user, log_actions['record_update'], {
                                 'record': str(record['_id'])})
                    # limpiar la cache
                    get_all.cache_clear()
                else:
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
                        'filepath': str(os.path.join(path, filename_new)),
                        'mime': mime,
                        'parent': [{
                            'id': resource_id,
                            'post_type': resource['post_type']
                        }],
                        'parents': resource['parents'],
                        'status': 'uploaded'
                    })
                    # insertar el record en la base de datos
                    new_record = mongodb.insert_record('records', record)
                    resp.append(str(new_record.inserted_id))
                    # registrar el log
                    register_log(current_user, log_actions['record_create'], {'record': {
                        'name': record.name,
                        'hash': record.hash,
                        'size': record.size,
                        'filepath': record.filepath
                    }})
                    # limpiar la cache
                    get_all.cache_clear()
            else:
                return jsonify({'message': 'File type not allowed'}), 400

        # retornar el resultado
        return resp

    except Exception as e:
        return {'msg': str(e)}, 500
