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

UPLOAD_FOLDER = os.path.abspath(os.path.dirname(__file__))
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

# Nuevo servicio para crear un record para un recurso
def create(resource_id, current_user, files):
    try:
        # Buscar el recurso en la base de datos
        resource = mongodb.get_record('resources', {'_id': ObjectId(resource_id)})
        # Si el recurso no existe, retornar error
        if not resource:
            return {'msg': 'Recurso no existe'}, 404
        
        resp = []

        print("HOLA MUNDO")
        
        for f in files:
            filename = secure_filename(f.filename)
            if allowedFile(filename):
                f.save(os.path.join(UPLOAD_FOLDER, filename))
                # calcular el hash 256 del archivo
                hash = hashlib.sha256()
                with open(os.path.join(UPLOAD_FOLDER, filename), 'rb') as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hash.update(chunk)
                # obtener el tamaño del archivo
                size = os.path.getsize(os.path.join(UPLOAD_FOLDER, filename))

                # se verifica si el hash del archivo ya existe en la base de datos
                record = mongodb.get_record('records', {'hash': hash.hexdigest()})
                # si el record existe, se agrega el recurso como padre
                if record:
                    # si el recurso ya es padre del record, retornar error
                    if resource_id in [parent['id'] for parent in record['parents']]:
                        return {'msg': 'El recurso ya tiene este archivo'}, 400
                    else:
                        resp.append(str(record['_id']))
                        # registrar el log
                        register_log(current_user, log_actions['record_update'], {'record': {
                            'name': record.name,
                            'hash': record.hash,
                            'size': record.size,
                            'filepath': record.filepath
                        }})
                        # limpiar la cache
                        get_all.cache_clear()
                else:
                    # crear un nuevo record
                    record = FileRecord(**{
                        'name': filename,
                        'hash': str(hash.hexdigest()),
                        'size': size,
                        'filepath': str(os.path.join(UPLOAD_FOLDER, filename))
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