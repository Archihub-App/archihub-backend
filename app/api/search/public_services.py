from flask import jsonify, request
from app.utils import IndexHandler
from app.api.users.services import has_right, has_role, get_user_rights
from app.utils.functions import get_resource_records, cache_type_roles, clear_cache
from app.api.resources.services import get_accessRights, get_resource_type, get_children
from app.api.types.services import get_icon
from app.utils.functions import get_access_rights
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.api.search.services import clean_elastic_response
import os

index_handler = IndexHandler.IndexHandler()
ELASTIC_INDEX_PREFIX = os.environ.get('ELASTIC_INDEX_PREFIX', '')


def get_resources_by_filters(body):
    try:
        post_types = body['post_type']

        for p in post_types:
            post_type_roles = cache_type_roles(p)
            user_accessRights = ['public']

            if post_type_roles['viewRoles']:
                return {'msg': 'No tiene permisos para obtener los recursos'}, 401

        query = {
            'track_total_hits': True,
            'query': {
                'bool': {
                    'filter': [
                        {
                            'terms': {
                                'post_type.keyword': body['post_type']
                            }
                        },
                        {
                            'term': {
                                'status.keyword': 'published'
                            }
                        },
                        {
                            'terms': {
                                'accessRights.keyword': user_accessRights
                            }
                        }
                    ],
                    'must': [
                        {
                            'query_string': {
                                'query': body['keyword'] if 'keyword' in body else '',
                                'default_operator': 'AND'
                            }
                        }
                    ]
                }
            },
            'size': 20,
            'from': body['page'] * 20,
            '_source': ['post_type', 'metadata.firstLevel.title', 'accessRights', '_id', 'ident', 'files', 'createdAt']
        }

        if 'keyword' in body:
            if len(body['keyword']) < 1:
                del query['query']['bool']['must']
            elif 'input_filters' in body:
                if len(body['input_filters']) > 0:
                    query['query']['bool']['must'][0]['query_string']['fields'] = body['input_filters']
        else:
            del query['query']['bool']['must']

        if 'files' in body:
            if body['files']:
                query['query']['bool']['filter'].append({
                    'range': {
                        'files': {
                            'gte': 1
                        }
                    }
                })

        if 'parents' in body:
            if body['parents']:
                query['query']['bool']['filter'].append({
                    'term': {
                        'parents.id': body['parents']['id']
                    }
                })

        if 'date_filters' in body:
            if len(body['date_filters']) > 0:
                for date_filter in body['date_filters']:
                    query['query']['bool']['filter'].append({
                        'range': {
                            date_filter['destiny']: {
                                'gte': date_filter['range'][0],
                                'lte': date_filter['range'][1]
                            }
                        }
                    })

        response = index_handler.search(ELASTIC_INDEX_PREFIX + '-resources', query)
        response = clean_elastic_response(response)

        # register_log(user, log_actions['search'], {'filters': body})

        return jsonify(response), 200
    except Exception as e:
        return {'msg': str(e)}, 500 
