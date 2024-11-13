from flask import jsonify
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from app.api.usertasks.models import UserTask, UserTaskUpdate
from datetime import datetime
from bson.objectid import ObjectId

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

def process_comments(comments):
    resp = []
    for comment in comments:
        out = ''
        out += f'{comment["user"]} '
        out += f'[{comment["createdAt"].strftime("%Y-%m-%d %H:%M:%S")}]: '
        out += f'{comment["comment"]}\n'
        resp.append(out)
    return '---------------\n'.join(resp)

def get_resource_tasks(resourceId):
    try:
        task = mongodb.get_record('usertasks', {'resourceId': resourceId, 'status': {'$in': ['pending', 'review', 'rejected']}}, fields={'user': 1, 'status': 1 ,'createdAt': 1, 'comment': 1})
        
        if task:
            task['_id'] = str(task['_id'])
            task['comment'] = process_comments(task['comment'])
            
            return task, 200
        else:
            return jsonify({'msg': 'No hay tareas pendientes para este recurso'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
def get_all_tasks(filters):
    try:
        f = {
            'status': {'$in': filters['status']},
            'user': filters['user'] if filters['user'] else {'$exists': True}
        }
        tasks = list(mongodb.get_all_records('usertasks', f, fields={'user': 1, 'status': 1 ,'createdAt': 1, 'resourceId': 1}))
        for task in tasks:
            task['_id'] = str(task['_id'])
            task['createdAt'] = task['createdAt'].strftime('%Y-%m-%d %H:%M:%S')
            from app.api.resources.services import get_resource_type
            task['resourceType'] = get_resource_type(task['resourceId'])
            
        total = mongodb.count('usertasks', f)
        resp = {
            'results': tasks,
            'total': total
        }
        return jsonify(resp), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
def get_editors():
    try:
        editors = list(mongodb.get_all_records('users', {'roles': 'editor'}, fields={'name': 1, 'username': 1}))
        for editor in editors:
            editor['value'] = editor['username']
            editor['label'] = editor['name']
            del editor['username']
            del editor['name']
            del editor['_id']
            
        return jsonify(editors), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
def create_task(body, user):
    try:
        if 'resourceId' not in body:
            return jsonify({'error': 'resourceId es requerido'}), 400
        if 'user' not in body:
            return jsonify({'error': 'user es requerido'}), 400
        if 'comment' not in body:
            return jsonify({'error': 'comment es requerido'}), 400
        
        if body['user'] == '':
            return jsonify({'error': 'user no puede ser vacío'}), 400
        if body['comment'] == '':
            return jsonify({'error': 'comment no puede ser vacío'}), 400
        if body['resourceId'] == '':
            return jsonify({'error': 'resourceId no puede ser vacío'}), 400
        
        task = mongodb.get_record('usertasks', {'resourceId': body['resourceId'], 'status': 'pending'})
        if task:
            return jsonify({'error': 'Ya existe una tarea pendiente para este recurso'}), 400
        
        body['status'] = 'pending'
        body['createdAt'] = datetime.now()
        body['comment'] =[{
            'user': user,
            'comment': body['comment'],
            'createdAt': datetime.now()
        }]
        
        userTask = UserTask(**body)
        task = mongodb.insert_record('usertasks', userTask)
        inserted_task = mongodb.get_record('usertasks', {'_id': ObjectId(str(task.inserted_id)), 'status': 'pending'}, fields={'user': 1, 'status': 1 ,'createdAt': 1, 'comment': 1})
        
        inserted_task['_id'] = str(inserted_task['_id'])
        inserted_task['createdAt'] = inserted_task['createdAt'].strftime('%Y-%m-%d %H:%M:%S')
        inserted_task['comment'] = process_comments(inserted_task['comment'])
        
        print(inserted_task)
        
        return jsonify(inserted_task), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
def update_task(id, body, user, isTeamLead):
    try:
        task = mongodb.get_record('usertasks', {'_id': ObjectId(id)})
        if not task:
            return jsonify({'error': 'No existe la tarea'}), 400
        
        if task['status'] == 'approved':
            return jsonify({'error': 'La tarea ya ha sido completada'}), 400
        
        if task['status'] == 'pending' and 'status' in body and body['status'] == 'review':
            if user != task['user']:
                return jsonify({'error': 'No es el usuario asignado para la tarea'}), 400
            
        if task['status'] == 'review' and 'status' in body and body['status'] == 'rejected':
            if not isTeamLead:
                return jsonify({'error': 'No tiene permisos suficientes'}), 401
            
        if task['status'] == 'review' and 'status' in body and body['status'] == 'approved':
            if not isTeamLead:
                return jsonify({'error': 'No tiene permisos suficientes'}), 401
            
        body['comment'] = [{
            'user': user,
            'comment': body['comment'],
            'createdAt': datetime.now()
            }, *task['comment']]
        
        update = UserTaskUpdate(**body)
        mongodb.update_record('usertasks', {'_id': ObjectId(id)}, update)
        updated_task = mongodb.get_record('usertasks', {'_id': ObjectId(id)}, fields={'user': 1, 'status': 1 ,'createdAt': 1, 'comment': 1})
        updated_task['_id'] = str(updated_task['_id'])
        updated_task['comment'] = process_comments(updated_task['comment'])
        
        if body['status'] == 'approved':
            return jsonify({'error': "No hay tareas sin completar"}), 400
        else:
            return jsonify(updated_task), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500