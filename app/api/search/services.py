from flask import jsonify, request
from app.utils import IndexHandler
import os

index_handler = IndexHandler.IndexHandler()
ELASTIC_INDEX_PREFIX = os.environ.get('ELASTIC_INDEX_PREFIX', '')


def get_resources_by_filters(body, user):
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
