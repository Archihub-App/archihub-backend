from app.api.userTasks.models import UserTask
from app.api.userTasks.models import UserTaskUpdate
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from flask import jsonify
from bson.objectid import ObjectId

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

def create_user_task(current_user, body):
    user_id = current_user['_id']
    task = UserTask(user_id=user_id, **body)
    task = mongodb.insert_record('userTasks', task)
    return jsonify({'msg': 'Tarea creada exitosamente', 'task': task}), 201

def delete_user_task(body):
    task = mongodb.delete_record('userTasks', {'_id': ObjectId(body['_id'])})
    return jsonify({'msg': 'Tarea eliminada exitosamente', 'task': task}), 200