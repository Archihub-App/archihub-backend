from flask import jsonify, request, send_file
from app.utils import DatabaseHandler
from bson import json_util
from functools import lru_cache
import json
from app.api.tasks.models import Task
from app.api.tasks.models import TaskUpdate
from datetime import datetime
from celery.result import AsyncResult
import os

mongodb = DatabaseHandler.DatabaseHandler()
USER_FILES_PATH = os.environ.get('USER_FILES_PATH', '')

# Funcion para parsear el resultado de una consulta a la base de datos
def parse_result(result):
    return json.loads(json_util.dumps(result))

# Nuevo servicio para recuperar las tasks de un usuario
def get_tasks(user, body):
    try:
        limit = 10
        skip = 0
        if 'page' in body:
            skip = body['page'] * limit

        # Obtener las tasks de un usuario
        tasks = mongodb.get_all_records('tasks', {'user': user}, sort=[('date', -1)], fields={'_id': 0, 'user': 0}, limit=limit, skip=skip)
        # Parsear el resultado
        tasks = parse_result(tasks)

        for t in tasks:
            if t['status'] == 'pending' or t['status'] == 'failed':
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

                elif not result.successful() and result.ready():
                    update = {
                        'status': 'failed',
                        'result': '',
                    }

                    task = TaskUpdate(**update)

                    mongodb.update_record('tasks', {'taskId': t['taskId']}, task)

                    t['status'] = 'failed'
                    t['result'] = ''

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
    get_tasks_total.cache_clear()

@lru_cache(maxsize=1000)
def get_tasks_total(user):
    try:
        # Obtener el total de tasks de un usuario
        total = mongodb.count('tasks', {'user': user})
        # Retornar el total
        return jsonify(total), 200
    except Exception as e:
        return {'msg': str(e)}, 500