import uuid
from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.utils import DatabaseHandler
from flask import request, send_file
from celery import shared_task
from dotenv import load_dotenv
import os
from app.api.resources.models import ResourceUpdate
from bson.objectid import ObjectId
import pandas as pd
import datetime

load_dotenv()

mongodb = DatabaseHandler.DatabaseHandler()

USER_FILES_PATH = os.environ.get('USER_FILES_PATH', '')
WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')
ORIGINAL_FILES_PATH = os.environ.get('ORIGINAL_FILES_PATH', '')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

class ExtendedPluginClass(PluginClass):
    def __init__(self, path, import_name, name, description, version, author, type, settings, actions, capabilities=None):
        super().__init__(path, __file__, import_name, name, description, version, author, type, settings, actions = actions, capabilities=None)

    def add_routes(self):
        @self.route('/bulk', methods=['POST'])
        @jwt_required()
        def generate_liquid_text():
            current_user = get_jwt_identity()
            body = request.get_json()
            
            if not self.has_role('admin', current_user) and not self.has_role('processing', current_user):
                return {'msg': 'No tiene permisos suficientes'}, 401

            task = self.bulk.delay(body, current_user)
            self.add_task_to_user(task.id, 'liquidText.generateLiquidText', current_user, 'msg')
            
            return {'msg': 'Se agregó la tarea a la fila de procesamientos'}, 201
        
    @shared_task(ignore_result=False, name='liquidText.generateLiquidText')
    def bulk(body, user):
        instance = ExtendedPluginClass('liquidText', '', **plugin_info)
        if 'records' not in body:
            filters = {
                'post_type': body['post_type']
            }
            
            if isinstance(body['post_type'], list):
                filters['post_type'] = {'$in': body['post_type']}   

            if 'parent' in body:
                if body['parent'] and len(body['resources']) == 0:
                    filters = {'$or': [{'parents.id': body['parent'], 'post_type': filters['post_type']}, {'_id': ObjectId(body['parent'])}]}
            
            if 'resources' in body:
                if body['resources']:
                    if len(body['resources']) > 0:
                        filters = {'_id': {'$in': [ObjectId(resource) for resource in body['resources']]}, **filters}
                
            # obtenemos los recursos
            resources = list(mongodb.get_all_records('resources', filters, fields={'_id': 1}))
            resources = [str(resource['_id']) for resource in resources]

            records_filters = {
                'parent.id': {'$in': resources}
            }
        else:
            records_filters = {'_id': {'$in': [ObjectId(record) for record in body['records']]}}

        if 'overwrite' in body and body['overwrite']:
            records_filters = {"$or": [{"processing.liquidText": {"$exists": False}, **records_filters}, {"processing.liquidText": {"$exists": True}, **records_filters}]}
        else:
            records_filters['processing.liquidText'] = {'$exists': False}
        
        records = list(mongodb.get_all_records('records', records_filters, fields={'_id': 1, 'mime': 1, 'filepath': 1, 'processing': 1}))
        
        for r in records:
            text_key = None
            for k in r['processing']:
                if r['processing'][k]['type'] and r['processing'][k]['type'] == 'av_transcribe':
                    text_key = k
                    break
                
            if text_key:
                text = r['processing'][text_key]['result']['text']
                update = {
                    'processing': r['processing']
                }
                
                update['processing']['liquidText'] = {
                    'type': 'liquidText',
                    'result': {
                        'text': text,
                        'status': 'completed',
                        'date': datetime.datetime.now()
                    }
                }
                
                instance.update_data('records', str(r['_id']), update)
        
        return 'ok'
    
plugin_info = {
    'name': 'Generar texto líquido',
    'description': 'Generar texto líquido a partir de los archivos catalogados en el sistema',
    'version': '0.1',
    'author': '',
    'type': ['bulk'],
    'settings': {
        'settings_bulk': []
    },
    'actions': [
        {
            'placement': 'detail_record',
            'record_type': ['audio', 'video'],
            'label': 'Crear texto líquido',
            'roles': ['admin', 'processing', 'editor'],
            'endpoint': 'bulk',
            'icon': 'WaterDrop, Article',
            'extraOpts': [
                {
                    'type': 'checkbox',
                    'label': 'Sobreescribir procesamientos existentes',
                    'id': 'overwrite',
                    'default': False,
                    'required': False,
                }
            ]
        },
    ]
}