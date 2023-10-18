from flask import jsonify, request
from app.utils import DatabaseHandler
from bson import json_util
from functools import lru_cache
import json
from app.api.tasks.models import Task
from app.api.tasks.models import TaskUpdate
from datetime import datetime
from celery.result import AsyncResult

mongodb = DatabaseHandler.DatabaseHandler()

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para recuperar las tasks de un usuario
@lru_cache(maxsize=1000)
def get_tasks(user):
    try:
        # Obtener las tasks de un usuario
        tasks = mongodb.get_all_records('tasks', {'user': user}, sort=[('date', -1)], fields={'_id': 0, 'user': 0})
        # Parsear el resultado
        tasks = parse_result(tasks)

        for t in tasks:
            if t['status'] == 'pending':
                result = AsyncResult(t['taskId'])

                if result.successful() and result.ready():
                    update = {
                        'status': 'completed',
                        'result': result.result,
                    }

                    task = TaskUpdate(**update)

                    mongodb.update_record('tasks', {'taskId': t['taskId']}, task)

                    t['status'] = 'completed'
                    t['result'] = result.result

        # Retornar las tasks
        return jsonify(tasks), 200
    except Exception as e:
        return {'msg': str(e)}, 500

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
        "date": datetime.now(),
    }

    task = Task(**new_task)

    # Guardar la tarea en la base de datos
    mongodb.insert_record('tasks', task)

    # limpiar cache
    get_tasks.cache_clear()