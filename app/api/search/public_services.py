from flask import jsonify, request
from app.utils import IndexHandler
from app.api.system.services import get_system_settings
from app.utils.functions import cache_type_roles
import os
from flask_babel import _

index_handler = IndexHandler.IndexHandler()
ELASTIC_INDEX_PREFIX = os.environ.get('ELASTIC_INDEX_PREFIX', '')


def get_resources_by_filters(body):
    try:
        capabilities, status = get_system_settings()
        capabilities = capabilities['capabilities']
        searchSource = body.get('searchSource', 'index')
        response = None
        
        if 'indexing' in capabilities and searchSource == 'index':
            from .utils import elasticUtils
            response = elasticUtils.get_resources_by_filters(body, None)
        elif 'vector_db' in capabilities and searchSource == 'vector':
            from .utils import vectorUtils
            response = vectorUtils.get_resources_by_filters(body, None)

        if response:
            return jsonify(response), 200
        else:
            raise Exception(_('No response from the indexing system'))
    except Exception as e:
        return {'msg': str(e)}, 500
