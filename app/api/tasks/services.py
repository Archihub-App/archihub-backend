from flask import jsonify, request, send_file
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from bson import json_util
import json
from app.api.tasks.models import Task
from app.api.tasks.models import TaskUpdate
from datetime import datetime
from celery.result import AsyncResult
import os

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()
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

        user_array = [user]

        if 'automatic' in body:
            user_array = ['automatic']

        # Obtener las tasks de un usuario
        tasks = mongodb.get_all_records('tasks', {'user': {'$in': user_array}}, sort=[('date', -1)], fields={'_id': 0, 'user': 0}, limit=limit, skip=skip)
        # Parsear el resultado
        tasks = parse_result(tasks)

        for t in tasks:
            if t['status'] == 'pending' or t['status'] == 'failed':
                result = AsyncResult(t['taskId'])

                if result.successful() and result.ready():
                    if type(result.result) == str:
                        update = {
                            'status': 'completed',
                            'result': result.result,
                        }

                        task = TaskUpdate(**update)

                        mongodb.update_record('tasks', {'taskId': t['taskId']}, task)

                        t['status'] = 'completed'
                        t['result'] = result.result
                    else:
                        update = {
                            'status': 'failed',
                            'result': '',
                        }

                        task = TaskUpdate(**update)

                        mongodb.update_record('tasks', {'taskId': t['taskId']}, task)

                        t['status'] = 'failed'
                        t['result'] = ''

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
def add_task(taskId, taskName, username, resultType):
    # Verificar si el usuario existe
    user = mongodb.get_record('users', {'username': username})
    if not user and username != 'automatic':
        return {'msg': 'El usuario no existe'}, 404

    new_task = {
        "taskId": taskId,
        "user": user['username'] if user else 'automatic',
        "status": "pending",
        "name": taskName,
        "resultType": resultType,
        "date": datetime.now(),
    }

    task = Task(**new_task)

    # Guardar la tarea en la base de datos
    mongodb.insert_record('tasks', task)
    get_tasks_total.invalidate_all()

@cacheHandler.cache.cache()
def get_tasks_total(user):
    try:
        # Obtener el total de tasks de un usuario
        total = mongodb.count('tasks', {'user': user})
        # Retornar el total
        return total
    except Exception as e:
        return {'msg': str(e)}, 500
    
# funcion para detener una tarea dado su id
def stop_task(taskId, user):
    try:
        # Obtener la tarea
        task = mongodb.get_record('tasks', {'taskId': taskId})
        # Verificar si la tarea existe
        if not task:
            return {'msg': 'La tarea no existe'}, 404
        
        # verificar que el usuario sea el dueño de la tarea
        if task['user'] != user:
            return {'msg': 'No tiene permisos para detener la tarea'}, 401

        # Obtener el resultado de la tarea
        result = AsyncResult(taskId)
        # Verificar si la tarea esta en estado pending
        if task['status'] == 'pending':
            # Detener la tarea
            result.revoke(terminate=True)
            # Actualizar el estado de la tarea
            update = {
                'status': 'failed',
                'result': '',
            }

            task = TaskUpdate(**update)

            mongodb.update_record('tasks', {'taskId': taskId}, task)

            return {'msg': 'La tarea se detuvo correctamente'}, 200
        else:
            return {'msg': 'La tarea no se puede detener'}, 400
    except Exception as e:
        return {'msg': str(e)}, 500