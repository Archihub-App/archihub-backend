import os
import requests
from requests.auth import HTTPBasicAuth
from ssl import create_default_context
from dotenv import load_dotenv
load_dotenv()


ELASTIC_USER = os.environ.get('ELASTIC_USER', '')
ELASTIC_PASSWORD = os.environ.get('ELASTIC_PASSWORD', '')
ELASTIC_DOMAIN = os.environ.get('ELASTIC_DOMAIN', '')
ELASTIC_PORT = os.environ.get('ELASTIC_PORT', '')
ELASTIC_INDEX_PREFIX = os.environ.get('ELASTIC_INDEX_PREFIX', '')


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
        return cls._instance

    def start(self):
        # get all the keys in the dictionary
        keys = self.get_aliases().keys()
        if len(keys) == 0:
            index_name = self.elastic_index_prefix + '-resources_1'
            from .index.spanish_settings import settings
            self.create_index(index_name, settings=settings)
            self.add_to_alias(ELASTIC_INDEX_PREFIX + '-resources', index_name)
        else:
            for k in self.get_aliases():
                print(k)

    def get_aliases(self):
        url = 'http://' + ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/_aliases'
        response = requests.get(url, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()
    
    def get_alias_indexes(self, alias):
        url = 'http://' + ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/_alias/' + alias
        response = requests.get(url, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()

    def create_index(self, index, settings=None, mapping=None):
        url = 'http://' + ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/' + index

        json = {}
        if settings:
            json['settings'] = settings
        if mapping:
            json['mappings'] = mapping

        response = requests.put(url, json=json, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        
        return response.json()

    def add_to_alias(self, alias, index):
        url = 'http://' + ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/_aliases'
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
        response = requests.post(url, json=body, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()

    def remove_from_alias(self, alias, index):
        url = 'http://' + ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/_aliases'
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
        response = requests.post(url, json=body, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()
    
    def delete_index(self, index):
        url = 'http://' + ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/' + index
        response = requests.delete(url, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()
    
    def reindex(self, source, dest):
        url = 'http://' + ELASTIC_DOMAIN + ':' + ELASTIC_PORT + '/_reindex'
        body = {
            'source': {
                'index': source
            },
            'dest': {
                'index': dest
            }
        }
        response = requests.post(url, json=body, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()

    def set_mapping(self, index, mapping):
        url = 'http://' + ELASTIC_DOMAIN + ':' + \
            ELASTIC_PORT + '/' + index + '/_mapping'
        response = requests.put(url, json=mapping, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        return response.json()
    
    def index_document(self, index, id, document):
        url = 'http://' + ELASTIC_DOMAIN + ':' + \
            ELASTIC_PORT + '/' + index + '/_doc/' + id
        response = requests.put(url, json=document, auth=HTTPBasicAuth(
            ELASTIC_USER, ELASTIC_PASSWORD))
        
        return response