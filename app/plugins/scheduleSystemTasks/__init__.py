import uuid
from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.utils import DatabaseHandler
from app.utils import HookHandler
from flask import request, send_file
from flask_babel import _
from celery import shared_task, current_app
from dotenv import load_dotenv
import os
from app.api.resources.models import ResourceUpdate
from bson.objectid import ObjectId
import pandas as pd
from app.api.types.services import get_all as get_all_types
import datetime
import shutil
import json

load_dotenv()

mongodb = DatabaseHandler.DatabaseHandler()
hookHandler = HookHandler.HookHandler()

USER_FILES_PATH = os.environ.get('USER_FILES_PATH', '')
WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')
ORIGINAL_FILES_PATH = os.environ.get('ORIGINAL_FILES_PATH', '')
TEMPORAL_FILES_PATH = os.environ.get('TEMPORAL_FILES_PATH', '')

class ExtendedPluginClass(PluginClass):
    def __init__(self, path, import_name, name, description, version, author, type, settings, actions, capabilities=None, **kwargs):
        super().__init__(path, __file__, import_name, name, description, version, author, type, settings, actions=actions, capabilities=capabilities, **kwargs)
        
    def add_routes(self):
        return
            
    def get_settings(self):
        @self.route('/settings/<type>', methods=['GET'])
        @jwt_required()
        def get_settings(type):
            try:
                current_user = get_jwt_identity()
                
                self.validate_roles(current_user, ['admin', 'processing'])
                    
                i = current_app.control.inspect()
                registered_tasks_dict = i.registered()
                systemTasks = []
                if registered_tasks_dict:
                    for worker, tasks in registered_tasks_dict.items():
                        systemTasks.extend(tasks)
                    systemTasks = sorted(list(set(systemTasks)))
                    
                current = self.get_plugin_settings()

                resp = self.get_translated_settings()

                resp['settings'][1]['fields'] = [
                    {
                        'type': 'select',
                        'id': 'task',
                        'default': '',
                        'options': [{'value': t, 'label': t} for t in systemTasks],
                        'required': True
                    },
                    {
                        'type': 'select',
                        'id': 'periodicity',
                        'default': '',
                        'options': [
                            {'value': 'every_x_minutes', 'label': _('Every x minutes')},
                            {'value': 'every_x_hours', 'label': _('Every x hours')},
                            {'value': 'once_a_day', 'label': _('Once a day')},
                            {'value': 'once_a_week', 'label': _('Once a week')},
                            {'value': 'once_a_month', 'label': _('Once a month')},
                            {'value': 'once_a_year', 'label': _('Once a year')}    
                        ],
                        'required': True
                    },
                    {
                        'type': 'number',
                        'id': 'interval_value',
                        'default': 1,
                        'required': False,
                        'idCondition': 'periodicity',
                        'conditionValue': ['every_x_minutes', 'every_x_hours']
                    },
                    {
                        'type': 'select',
                        'id': 'hour_execution',
                        'default': '',
                        'options': [{'value': f'{i:02d}:00', 'label': f'{i:02d}:00'} for i in range(24)],
                        'required': False,
                        'idCondition': 'periodicity',
                        'conditionValue': ['once_a_day', 'once_a_week', 'once_a_month', 'once_a_year']
                    }
                ]
                
                if current is None or current == {}:
                    resp['settings'][1]['default'] = []
                    
                else:
                    resp['settings'][1]['default'] = current['schedule_tasks']

                
                if type == 'all':
                    return resp
                elif type == 'settings':
                    return resp['settings']
                else:
                    return resp['settings_' + type]
            except Exception as e:
                return {'msg': str(e)}, 500
            
        @self.route('/settings', methods=['POST'])
        @jwt_required()
        def set_settings_update():
            try:
                current_user = get_jwt_identity()

                self.validate_roles(current_user, ['admin', 'processing'])
                
                body = request.form.to_dict()
                data = body['data']
                data = json.loads(data)

                tasks = data['schedule_tasks']
                for t in tasks:
                    if not t.get('task') or not t.get('periodicity'):
                        return {'msg': _('Missing required fields')}, 400

                    if t['periodicity'] in ['every_x_minutes', 'every_x_hours']:
                        try:
                            interval_value = int(t.get('interval_value'))
                        except (TypeError, ValueError):
                            return {'msg': _('Interval value must be a positive integer')}, 400

                        if interval_value <= 0:
                            return {'msg': _('Interval value must be a positive integer')}, 400

                        t['interval_value'] = interval_value
                    elif not t.get('hour_execution'):
                        return {'msg': _('Missing required fields')}, 400

                self.set_plugin_settings(data)

                return {'msg': _('Settings updated')}, 200
            
            except Exception as e:
                return {'msg': str(e)}, 500
    
plugin_info = {
    'name': 'Programador de tareas',
    'description': 'Plugin para la gestión de tareas programadas',
    'version': '0.1',
    'author': '',
    'type': ['settings'],
    'capabilities': ['scheduler'],
    'settings': {
        'settings': [
            {
                'type': 'instructions',
                'title': 'Instrucciones',
                'text': 'Desde aquí puedes programar tareas del sistema para que se ejecuten periódicamente. Puedes seleccionar la tarea, la periodicidad y la hora de ejecución o un intervalo. Si la periodicidad es "Cada x minutos" o "Cada x horas", debes indicar el intervalo. Si la periodicidad es "Una vez a la semana", se ejecutará el día lunes a la hora seleccionada. Si la periodicidad es "Una vez al mes", se ejecutará el primer día del mes a la hora seleccionada. Si la periodicidad es "Una vez al año", se ejecutará el primer día del año a la hora seleccionada.',
            },
            {
                'type': 'multiple',
                'title': 'Tareas a programar',
                'id': 'schedule_tasks',
                'fields': []
            }
        ],
    },
    'actions': []
}