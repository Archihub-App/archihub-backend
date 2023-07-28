from flask import jsonify
from app.utils import DatabaseHandler
from bson import json_util
import json
from app.api.logs.models import Log
from datetime import datetime
from app.utils import LogActions

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Funcion para obtener la fecha actual
def get_current_date():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")

# Nuevo servicio para registrar un log
def register_log(username, action, metadata=None):
    # Obtener la fecha actual
    date = get_current_date()
    # Crear instancia de Log con el username, la acción y la fecha
    log = Log(username=username, action=action, date=date, metadata=metadata)
    # Insertar log en la base de datos
    mongodb.insert_record('logs', log)
    # Retornar mensaje de éxito
    return jsonify({'msg': 'Log registrado exitosamente'}), 200