from flask import jsonify, request
from app.utils.functions import get_access_rights
from app.api.system.services import get_system_settings
import os
from flask_babel import _


def get_resources_by_filters(body, user):
    try:
        capabilities = get_system_settings()
        capabilities = capabilities['capabilities']
        response = {}
        
        if 'indexing' in capabilities:
            from .utils import elasticUtils
            response = elasticUtils.get_resources_by_filters(body, user)

        return jsonify(response), 200
    except Exception as e:
        return {'msg': str(e)}, 500