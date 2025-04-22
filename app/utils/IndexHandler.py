import os
import requests
from requests.auth import HTTPBasicAuth
from ssl import create_default_context
from dotenv import load_dotenv
load_dotenv()
from app.utils.functions import get_access_rights


ELASTIC_USER = os.environ.get('ELASTIC_USER', '')
ELASTIC_PASSWORD = os.environ.get('ELASTIC_PASSWORD', '')
ELASTIC_DOMAIN = os.environ.get('ELASTIC_DOMAIN', '')
ELASTIC_PORT = os.environ.get('ELASTIC_PORT', '')
ELASTIC_INDEX_PREFIX = os.environ.get('ELASTIC_INDEX_PREFIX', '')
ELASTIC_CERT = os.environ.get('ELASTIC_CERT', '')

class IndexHandler:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.elastic_user = ELASTIC_USER
            cls._instance.elastic_password = ELASTIC_PASSWORD
            cls._instance.elastic_domain = ELASTIC_DOMAIN
            cls._instance.elastic_port = ELASTIC_PORT
            cls._instance.elastic_index_prefix = ELASTIC_INDEX_PREFIX
            if ELASTIC_CERT != '':
                cls._instance.ssl_context = ELASTIC_CERT
            else:
                cls._instance.ssl_context = None
        return cls._instance

    def start(self):
        # get all the keys in the dictionary
        keys = self.get_aliases().keys()
        if len(keys) == 0:
            self.start_new_index()
        # else:
        #     for k in self.get_aliases():
        #         print(k)

    def start_new_index(self, mapping=None, slug='resources'):
        index_name = self.elastic_index_prefix + '-' + slug + '_1'
        from .index.spanish_settings import settings
        self.create_index(index_name, settings=settings, mapping=mapping)
        self.add_to_alias(ELASTIC_INDEX_PREFIX + '-' + slug, index_name)

    def get_aliases(self):
        url = ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/_aliases'
        if self.ssl_context:
            response = requests.get(url, auth=HTTPBasicAuth(
                ELASTIC_USER, ELASTIC_PASSWORD), verify=self.ssl_context)
        else:
            response = requests.get(url, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()
    
    def get_alias_indexes(self, alias):
        url = ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/_alias/' + alias
        if self.ssl_context:
            response = requests.get(url, auth=HTTPBasicAuth(
                ELASTIC_USER, ELASTIC_PASSWORD), verify=self.ssl_context)
        else:
            response = requests.get(url, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        
        print(response.text, response.status_code)
        return response.json()

    def create_index(self, index, settings=None, mapping=None):
        url = ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/' + index

        json = {}
        if settings:
            json['settings'] = settings
        if mapping:
            json['mappings'] = mapping

        if self.ssl_context:
            response = requests.put(url, json=json, auth=HTTPBasicAuth(
                ELASTIC_USER, ELASTIC_PASSWORD), verify=self.ssl_context)
        else:
            response = requests.put(url, json=json, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        
        return response.json()

    def add_to_alias(self, alias, index):
        url = ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/_aliases'
        body = {
            'actions': [
                {
                    'add': {
                        'index': index,
                        'alias': alias
                    }
                }
            ]
        }
        if self.ssl_context:
            response = requests.post(url, json=body, auth=HTTPBasicAuth(
                ELASTIC_USER, ELASTIC_PASSWORD), verify=self.ssl_context)
        else:
            response = requests.post(url, json=body, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()

    def remove_from_alias(self, alias, index):
        url = ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/_aliases'
        body = {
            'actions': [
                {
                    'remove': {
                        'index': index,
                        'alias': alias
                    }
                }
            ]
        }
        if self.ssl_context:
            response = requests.post(url, json=body, auth=HTTPBasicAuth(
                ELASTIC_USER, ELASTIC_PASSWORD), verify=self.ssl_context)
        else:
            response = requests.post(url, json=body, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()
    
    def delete_index(self, index):
        url = ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/' + index
        if self.ssl_context:
            response = requests.delete(url, auth=HTTPBasicAuth(
                ELASTIC_USER, ELASTIC_PASSWORD), verify=self.ssl_context)
        else:
            response = requests.delete(url, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()
    
    def delete_all_documents(self, index):
        url = ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/' + index + '/_delete_by_query'
        body = {
            'query': {
                'match_all': {}
            }
        }
        if self.ssl_context:
            response = requests.post(url, json=body, auth=HTTPBasicAuth(
                ELASTIC_USER, ELASTIC_PASSWORD), verify=self.ssl_context)
        else:
            response = requests.post(url, json=body, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()
    
    def delete_document(self, index, id):
        url = ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/' + index + '/_doc/' + id
        if self.ssl_context:
            response = requests.delete(url, auth=HTTPBasicAuth(
                ELASTIC_USER, ELASTIC_PASSWORD), verify=self.ssl_context)
        else:
            response = requests.delete(url, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        
        return response.json()
    
    def reindex(self, source, dest):
        url = ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/_reindex'
        body = {
            'source': {
                'index': source
            },
            'dest': {
                'index': dest
            }
        }
        if self.ssl_context:
            response = requests.post(url, json=body, auth=HTTPBasicAuth(
                ELASTIC_USER, ELASTIC_PASSWORD), verify=self.ssl_context)
        else:
            response = requests.post(url, json=body, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()

    def set_mapping(self, index, mapping):
        url = ELASTIC_DOMAIN + ':' + \
            ELASTIC_PORT + '/' + index + '/_mapping'
        if self.ssl_context:
            response = requests.put(url, json=mapping, auth=HTTPBasicAuth(
                ELASTIC_USER, ELASTIC_PASSWORD), verify=self.ssl_context)
        else:
            response = requests.put(url, json=mapping, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()
    
    def index_document(self, index, id, document):
        url = ELASTIC_DOMAIN + ':' + \
            ELASTIC_PORT + '/' + index + '/_doc/' + id
        if self.ssl_context:
            response = requests.put(url, json=document, auth=HTTPBasicAuth(
                ELASTIC_USER, ELASTIC_PASSWORD), verify=self.ssl_context)
        else:
            response = requests.put(url, json=document, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        
        return response
    
    def search(self, index, query):
        url = ELASTIC_DOMAIN + ':' + \
            ELASTIC_PORT + '/' + index + '/_search'
        if self.ssl_context:
            response = requests.post(url, json=query, auth=HTTPBasicAuth(
                ELASTIC_USER, ELASTIC_PASSWORD), verify=self.ssl_context)
        else:
            response = requests.post(url, json=query, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()
    
    def clean_elastic_search_response(self, response):
        rights_system = get_access_rights()
        rights_system = rights_system['options']

        if 'hits' in response:
            response = response['hits']

            resources = []
            for r in response['hits']:
                index = True
                new = {**r['_source'], 'id': r['_id']}
                
                if 'accessRights' in new:
                    rights = new['accessRights']
                    if rights == 'public':
                        del new['accessRights']
                    else:
                        right = [r for r in rights_system if r['id'] == new['accessRights']]
                        if len(right) > 0:
                            new['accessRights'] = right[0]['term']
                        else:
                            index = False
                if 'highlight' in r and 'text' in r['highlight']:
                    # Replace the original text with the highlighted version
                    new['text'] = r['highlight']['text'][0]
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
            
    def clean_elastic_aggregation_response(self, response):
        if 'aggregations' in response:
            response = response['aggregations']
            return response
        return []
