from flask import jsonify, request
from app.utils import DatabaseHandler
from bson import json_util
import json
from app.api.forms.models import Form

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para obtener todos los estándares de metadatos
def get_all():
    # Obtener todos los estándares de metadatos
    forms = mongodb.get_all_records('forms')
    # Retornar forms
    return jsonify(forms), 200