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
from app.api.types.services import add_resource
from app.api.types.services import is_hierarchical
from app.api.types.services import get_icon
from app.api.types.services import get_metadata
from app.api.system.services import validate_text
from app.api.system.services import validate_text_array
from app.api.system.services import validate_text_regex
from app.api.system.services import get_value_by_path
from werkzeug.utils import secure_filename
import os
import hashlib
import magic
import uuid

# get path of root folder

UPLOAD_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
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

# Nuevo servicio para borrar un parent de un record
def delete_parent(resource_id, parent_id, current_user):
    try:
        # Buscar el recurso en la base de datos
        resource = mongodb.get_record('resources', {'_id': ObjectId(resource_id)})
        # Si el recurso no existe, retornar error
        if not resource:
            return {'msg': 'Recurso no existe'}, 404
        
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
        record['parent'] = [x for x in record['parent'] if x['id'] != resource_id]

        array_parents = [x['id'] for x in resource['parents']]
        # Si el record tiene parents del recurso como parents, eliminarlos
        # el parent es de tipo dict y tiene los campos id y post_type
        record['parents'] = [x for x in record['parents'] if x['id'] not in array_parents]

        status = record['status']
        # Si el record no tiene parents, cambiar el status a deleted
        if len(record['parents']) == 0:
            status = 'deleted'
        
        # Actualizar el record
        update = FileRecordUpdate(**{
            'parent': record['parent'],
            'parents': record['parents'],
            'status': status
        })
        mongodb.update_record('records', {'_id': record['_id']}, update)
        
        # Registrar el log
        register_log(current_user, log_actions['record_update'], {'record': str(record['_id'])})
        # Limpiar la cache
        get_all.cache_clear()
        
        # Retornar el resultado
        return {'msg': 'Parent eliminado exitosamente'}, 200
        
    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para crear un record para un recurso
def create(resource_id, current_user, files):
    try:
        # Buscar el recurso en la base de datos
        resource = mongodb.get_record('resources', {'_id': ObjectId(resource_id)})
        # Si el recurso no existe, retornar error
        if not resource:
            return {'msg': 'Recurso no existe'}, 404
        
        resp = []

        for f in files:
            filename = secure_filename(f.filename)
            if allowedFile(filename):
                # generar un nombre unico para el archivo
                filename_new = str(uuid.uuid4()) + '.' + filename.rsplit('.', 1)[1].lower()
                # coger la fecha actual y convertirla a string de la forma YYYY/MM/DD
                date = datetime.datetime.now().strftime("%Y/%m/%d")
                # hacer un path en base a la fecha actual
                path = os.path.join(UPLOAD_FOLDER, date)
                # crear el directorio para guardar el archivo usando la ruta date
                if not os.path.exists(path):
                    os.makedirs(path)
                f.save(os.path.join(path, filename))
                # renombrar el archivo
                os.rename(os.path.join(path, filename), os.path.join(path, filename_new))
                # calcular el hash 256 del archivo
                hash = hashlib.sha256()
                with open(os.path.join(path, filename_new), 'rb') as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hash.update(chunk)
                

                # se verifica si el hash del archivo ya existe en la base de datos
                record = mongodb.get_record('records', {'hash': hash.hexdigest()})
                # si el record existe, se agrega el recurso como padre
                if record:
                    # eliminar el archivo que se subio
                    os.remove(os.path.join(path, filename_new))

                    resp.append(str(record['_id']))

                    update_dict = {
                        'parent': [{
                            'id': resource_id,
                            'post_type': resource['post_type']
                        }, *record['parent']],
                        'parents': [*resource['parents'], *record['parents']]
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
                    mongodb.update_record('records', {'_id': record['_id']}, update)

                    # registrar el log
                    register_log(current_user, log_actions['record_update'], {'record': str(record['_id'])})
                    # limpiar la cache
                    get_all.cache_clear()
                else:
                    # obtener el tamaño del archivo
                    size = os.path.getsize(os.path.join(path, filename_new))

                    # usar magic para obtener el tipo de archivo
                    mime = magic.from_file(os.path.join(path, filename_new), mime=True)

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