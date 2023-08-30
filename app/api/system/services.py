from flask import jsonify, request
from app.utils import DatabaseHandler
from bson import json_util
from functools import lru_cache
import json
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.api.system.models import Option
from app.api.system.models import OptionUpdate

mongodb = DatabaseHandler.DatabaseHandler('sim-backend-prod')

# Funcion para actualizar el registro resources-schema en la colección system
def update_resources_schema(schema):
    try:
        # Obtener el registro resources-schema de la colección system
        resources_schema = mongodb.get_record('system', {'name': 'resources-schema'})
        # Si el registro no existe, crearlo
        if not resources_schema:
            new = Option(**{'name': 'resources-schema', 'data': schema})
            mongodb.insert_record('system', new)
        # Si el registro existe, actualizarlo
        else:
            update = OptionUpdate(**{'data': schema})
            mongodb.update_record('system', {'name': 'resources-schema'}, update)

        # Retornar el resultado
        return {'msg': 'Schema actualizado exitosamente'}
    except Exception as e:
        raise Exception('Error al actualizar el schema de los recursos')