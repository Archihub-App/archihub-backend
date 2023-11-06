from flask import jsonify, request
from app.utils import IndexHandler
from app.api.users.services import has_right, has_role
from app.utils.functions import get_resource_records, cache_type_roles, clear_cache
from app.api.resources.services import get_accessRights, get_resource_type
import os

index_handler = IndexHandler.IndexHandler()
ELASTIC_INDEX_PREFIX = os.environ.get('ELASTIC_INDEX_PREFIX', '')


def get_resources_by_filters(body, user):
    post_type_roles = cache_type_roles(body['post_type'])
    if post_type_roles['viewRoles']:
        canView = False
        for r in post_type_roles['viewRoles']:
            if has_role(user, r) or has_role(user, 'admin'):
                canView = True
                break
        if not canView:
            return {'msg': 'No tiene permisos para obtener los recursos'}, 401
        
    query = {
        'query': {
            'bool': {
                'filter': [
                    {
                        'term': {
                            'post_type': body['post_type']
                        }
                    }
                ],
                'must': [
                    {
                        'query_string': {
                            'query': body['keyword'],
                            'default_operator': 'AND'
                        }
                    }
                ]
            }
        },
        'size': 20,
        '_source': ['post_type', 'metadata.firstLevel.title', 'accessRights', '_id']
    }

    response = index_handler.search(ELASTIC_INDEX_PREFIX + '-resources', query)
    response = clean_elastic_response(response)

    return jsonify(response), 200

def get_tree_by_query(root, available, user):
    list_available = available.split('|')

    return 'ok'

def clean_elastic_response(response):
    response = response['hits']

    resources = []
    for r in response['hits']:
        resources.append({**r['_source'], 'id': r['_id']})

    response = {
        'total': response['total']['value'],
        'resources': resources
    }

    return response
