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
from flask_babel import _

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
        tasks = mongodb.get_all_records('tasks', {'user': {'$in': user_array}}, sort=[
                                        ('date', -1)], limit=limit, skip=skip)
        # Parsear el resultado
        tasks = parse_result(tasks)

        for t in tasks:
            t['user'] = t['user'] if t['user'] != 'automatic' else 'system'
            if t['status'] == 'pending' or t['status'] == 'failed':
                result = AsyncResult(t['taskId'])

                if result.successful() and result.ready():
                    if type(result.result) == str:
                        update = {
                            'status': 'completed',
                            'result': result.result,
                        }

                        task = TaskUpdate(**update)

                        mongodb.update_record(
                            'tasks', {'taskId': t['taskId']}, task)

                        t['status'] = 'completed'
                        t['result'] = result.result
                    else:
                        update = {
                            'status': 'failed',
                            'result': '',
                        }

                        task = TaskUpdate(**update)

                        mongodb.update_record(
                            'tasks', {'taskId': t['taskId']}, task)

                        t['status'] = 'failed'
                        t['result'] = ''

                elif not result.successful() and result.ready():
                    update = {
                        'status': 'failed',
                        'result': '',
                    }

                    task = TaskUpdate(**update)

                    mongodb.update_record(
                        'tasks', {'taskId': t['taskId']}, task)

                    t['status'] = 'failed'
                    t['result'] = ''

        # Retornar las tasks
        return jsonify(tasks), 200
    except Exception as e:
        return {'msg': str(e)}, 500


def has_task(user, name):
    try:
        # Obtener la tarea
        task = mongodb.get_record(
            'tasks', {'user': user, 'name': name, 'status': 'pending'})

        if task:
            result = AsyncResult(task['taskId'])

            if result.successful() and result.ready():
                if type(result.result) == str:
                    update = {
                        'status': 'completed',
                        'result': result.result,
                    }

                    task = TaskUpdate(**update)

                    mongodb.update_record(
                        'tasks', {'taskId': task['taskId']}, task)

                    t['status'] = 'completed'
                    t['result'] = result.result
                    return False
                else:
                    update = {
                        'status': 'failed',
                        'result': '',
                    }

                    task = TaskUpdate(**update)

                    mongodb.update_record(
                        'tasks', {'taskId': task['taskId']}, task)

                    t['status'] = 'failed'
                    t['result'] = ''
                
                return False
            elif not result.successful() and result.ready():
                update = {
                    'status': 'failed',
                    'result': '',
                }

                task = TaskUpdate(**update)

                mongodb.update_record(
                    'tasks', {'taskId': task['taskId']}, task)

                t['status'] = 'failed'
                t['result'] = ''
                return False
        # Verificar si la tarea existe
        if not task:
            return False
        # Verificar si el usuario es el dueño de la tarea
        if task['user'] != user:
            return False
    except Exception as e:
        print(str(e))
        return True

# Nuevo servicio para agregar una tarea a la base de datos asignandola a un usuario


def add_task(taskId, taskName, username, resultType, params={}):
    # Verificar si el usuario existe
    user = mongodb.get_record('users', {'username': username})
    if not user and username != 'automatic' and username != 'system':
        return {'msg': _('User not found')}, 404
    
    new_task = {
        "taskId": taskId,
        "user": user['username'] if user else 'automatic',
        "status": "pending",
        "name": taskName,
        "resultType": resultType,
        "date": datetime.now(),
        "params": params,
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
            return {'msg': _('Task not found')}, 404

        # Obtener el resultado de la tarea
        result = AsyncResult(taskId)
        # Verificar si la tarea esta en estado pending
        if task['status'] == 'pending':
            # Detener la tarea
            result.revoke(terminate=True)
            
            # eliminar la tarea de la base de datos
            mongodb.delete_record('tasks', {'taskId': taskId})
            # eliminar la tarea de la cache
            get_tasks_total.invalidate_all()

            return {'msg': _('Task stopped successfully')}, 200
        else:
            return {'msg': _('Task cannot be stopped')}, 400
    except Exception as e:
        return {'msg': str(e)}, 500
