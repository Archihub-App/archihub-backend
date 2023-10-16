from flask import jsonify, request
from app.utils import DatabaseHandler
from bson import json_util
from functools import lru_cache
import json
from app.api.tasks.models import Task
from app.api.tasks.models import TaskUpdate

mongodb = DatabaseHandler.DatabaseHandler()

# Nuevo servicio para agregar una tarea a la base de datos asignandola a un usuario
def add_task(taskId, taskName, user, resultType):

    # Verificar si el usuario existe
    user = mongodb.get_record('users', {'username': user})
    if not user:
        return {'msg': 'El usuario no existe'}, 404

    new_task = {
        "taskId": taskId,
        "user": user['username'],
        "status": "pending",
        "name": taskName,
        "resultType": resultType,
        "date": None,
    }

    task = Task(**new_task)

    # Guardar la tarea en la base de datos
    mongodb.insert_record('tasks', task)