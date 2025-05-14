from app.api.users.services import has_right, has_role, get_user_rights
from app.utils.functions import get_resource_records, cache_type_roles, clear_cache
from app.utils import IndexHandler
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from flask_babel import _
import os

index_handler = IndexHandler.IndexHandler()
ELASTIC_INDEX_PREFIX = os.environ.get('ELASTIC_INDEX_PREFIX', '')

def get_resources_by_filters(body, user):
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
                raise Exception(_('You don\'t have the required authorization'))

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
                
    if 'location_filters' in body:
        if len(body['location_filters']) > 0:
            for location_filter in body['location_filters']:
                location_field = location_filter['destiny']
                location_values = location_filter['value']
                
                for location_value in location_values:
                    highest_level = None
                    level_id = None
                    parent_id = None
                    
                    for i in range(2, -1, -1):
                        level_key = f'level_{i}'
                        if level_key in location_value and location_value[level_key] is not None:
                            highest_level = i
                            level_id = location_value[level_key]['ident']
                            if i > 0:
                                parent_key = f'level_{i-1}'
                                if parent_key in location_value and location_value[parent_key] is not None:
                                    parent_id = location_value[parent_key]['ident']
                            break
                    
                    if highest_level is not None and level_id is not None:
                        shape_query = {
                            "query": {
                                "bool": {
                                    "must": [
                                        {"term": {"properties.admin_level": highest_level}},
                                        {"term": {"properties.ident": level_id}}
                                    ]
                                }
                            }
                        }
                        
                        if parent_id is not None:
                            shape_query["query"]["bool"]["must"].append(
                                {"term": {"properties.parent": parent_id}}
                            )
                        
                        shape_result = index_handler.search(ELASTIC_INDEX_PREFIX + '-shapes', shape_query)
                        
                        if 'hits' in shape_result and 'hits' in shape_result['hits'] and len(shape_result['hits']['hits']) > 0:
                            shape_id = shape_result['hits']['hits'][0]['_id']
                            query['query']['bool']['filter'].append({
                                'geo_shape': {
                                    location_field: {
                                        'indexed_shape': {
                                            'index': ELASTIC_INDEX_PREFIX + '-shapes',
                                            'id': shape_id,
                                            'path': 'geometry'
                                        },
                                        'relation': 'intersects'
                                    }
                                }
                            })

    response = index_handler.search(ELASTIC_INDEX_PREFIX + '-resources', query)
    
    response = index_handler.clean_elastic_search_response(response)

    register_log(user, log_actions['search'], {'filters': body})
    
    return response