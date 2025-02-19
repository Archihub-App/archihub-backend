from flask import jsonify, request
from app.utils import IndexHandler
from app.api.users.services import has_right, has_role, get_user_rights
from app.utils.functions import get_resource_records, cache_type_roles, clear_cache
from app.api.resources.services import get_accessRights, get_resource_type, get_children
from app.api.types.services import get_icon
from app.utils.functions import get_access_rights
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
import os
from flask_babel import _

index_handler = IndexHandler.IndexHandler()
ELASTIC_INDEX_PREFIX = os.environ.get('ELASTIC_INDEX_PREFIX', '')


def get_resources_by_filters(body, user):
    try:
        post_types = body['post_type']
        sort_direction = 1 if body.get('sortOrder', 'asc') == 'asc' else -1
        sortBy = body.get('sortBy', 'createdAt')

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
                    return {'msg': _('You don\'t have the required authorization')}, 401

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
                                'status.keyword': body['status'] if 'status' in body else 'published'
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
            'from': body['page'] * 20 if 'page' in body else 0,
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

        register_log(user, log_actions['search'], {'filters': body})

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
            '_source': ['post_type', 'metadata.firstLevel.title', 'parent', '_id', 'createdAt']
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
