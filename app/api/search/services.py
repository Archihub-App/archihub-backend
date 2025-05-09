from flask import jsonify, request
from app.utils.functions import get_access_rights
from app.api.system.services import get_system_settings
from flask_babel import _


def get_resources_by_filters(body, user):
    try:
        capabilities = get_system_settings()
        capabilities = capabilities['capabilities']
        response = None
        
        if 'indexing' in capabilities:
            from .utils import elasticUtils
            response = elasticUtils.get_resources_by_filters(body, user)

        if response:
            return jsonify(response), 200
        else:
            raise Exception(_('No response from the indexing system'))
    except Exception as e:
        return {'msg': str(e)}, 500