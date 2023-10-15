from flask import jsonify, request
from app.utils import DatabaseHandler
from bson import json_util
from functools import lru_cache
import json
from app.api.tasks.models import Task
from app.api.tasks.models import TaskUpdate

mongodb = DatabaseHandler.DatabaseHandler()

# Nuevo servicio para agregar una tarea a la base de datos asignandola a un usuario
def add_task(task, user_id):
    # Obtener el usuario
    user = mongodb.get_user_by_id(user_id)
    # Verificar si el usuario existe
    if not user:
        return {'msg': 'El usuario no existe'}, 404