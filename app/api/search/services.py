from flask import jsonify, request
from app.utils import IndexHandler
from app.api.users.services import has_right, has_role, get_user_rights
from app.utils.functions import get_resource_records, cache_type_roles, clear_cache
from app.api.resources.services import get_accessRights, get_resource_type, get_children
from app.api.types.services import get_icon
from app.utils.functions import get_access_rights
import os

index_handler = IndexHandler.IndexHandler()
ELASTIC_INDEX_PREFIX = os.environ.get('ELASTIC_INDEX_PREFIX', '')


def get_resources_by_filters(body, user):
    try:
        post_types = body['post_type']

        for p in post_types:
            post_type_roles = cache_type_roles(p)
            user_accessRights = get_user_rights(user)
            user_accessRights = user_accessRights + ['public']

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
                            'terms': {
                                'post_type.keyword': body['post_type']
                            }
                        },
                        {
                            'term': {
                                'status.keyword': body['status']
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

        if 'parents' in body:
            if body['parents']:
                query['query']['bool']['filter'].append({
                    'term': {
                        'parents.id': body['parents']['id']
                    }
                })


        response = index_handler.search(ELASTIC_INDEX_PREFIX + '-resources', query)
        response = clean_elastic_response(response)

        return jsonify(response), 200
    except Exception as e:
        return {'msg': str(e)}, 500 

def get_tree_by_query(body, root, available, user):
    try:
        list_available = available.split('|')

        query = {
            'query': {
                'bool': {
                    'filter': [],
                    'must_not': [],
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
            '_source': ['post_type', 'metadata.firstLevel.title', 'parent', '_id']
        }

        if root == 'all':
            query['query']['bool']['filter'].append({
                'terms': {
                    'post_type': list_available
                }
            })
        else:
            query['query']['bool']['filter'].append({
                'terms': {
                    'post_type': list_available
                },
                'term': {
                    'parent.id': root
                }
            })

        response = index_handler.search(ELASTIC_INDEX_PREFIX + '-resources', query)
        response = clean_elastic_response(response)

        resources = [{'name': re['metadata']['firstLevel']['title'], 'post_type': re['post_type'], 'id': str(
                re['id'])} for re in response['resources']]

        for r in resources:
            r['icon'] = get_icon(r['post_type'])
            r['children'] = get_children(r['id'], available)

        return resources, 200

    except Exception as e:
        return {'msg': str(e)}, 500

def clean_elastic_response(response):
    rights_system = get_access_rights()
    rights_system = rights_system['options']

    if 'hits' in response:
        response = response['hits']

        resources = []
        for r in response['hits']:
            index = True
            new = {**r['_source'], 'id': r['_id']}
            rights = new['accessRights']
            if rights == 'public':
                del new['accessRights']
            else:
                right = [r for r in rights_system if r['id'] == new['accessRights']]
                if len(right) > 0:
                    new['accessRights'] = right[0]['term']
                else:
                    index = False

            if index:
                resources.append(new)

        response = {
            'total': response['total']['value'],
            'resources': resources
        }

        return response
    else:
        return {
            'total': 0,
            'resources': []
        }
