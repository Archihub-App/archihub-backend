import pymongo
import sys
import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv
load_dotenv()

MONGO_ADMIN = 'MONGO_INITDB_ROOT_PASSWORD'
MONGO_DATABASE = 'MONGO_DATABASE'
MONGO_IP_SERVER = 'MONGO_IP_SERVER'
MONGO_PORT = 'MONGO_PORT'
MONGO_USER = 'MONGO_INITDB_ROOT_USERNAME'

# Esta clase sirve para conectarse a la base de datos de MongoDB

class MongoConector:
    def __init__(self):
        self.ip_server= os.environ.get(MONGO_IP_SERVER, 'localhost').split(',')
        self.admin = os.environ.get(MONGO_ADMIN, '7bOS9*NkX41M')
        self.user = os.environ.get(MONGO_USER, 'admin')
        self.database = os.environ.get(MONGO_DATABASE, 'sim-backend-prod')
        self.port = os.environ.get(MONGO_PORT, '27017')
        self.simpledatabase = os.environ.get(MONGO_DATABASE, 'sim-backend-prod')

    def getMongoURI(self):
        mongourl = "mongodb://"
        for x in range(len(self.ip_server)):
            if(x > 0):
                mongourl = mongourl + "," + self.ip_server[x] +":" + self.port
            elif self.user == '':
                mongourl = mongourl + "admin:"+self.admin+"@"+self.ip_server[x]+":" + self.port
            elif self.user != '':
                mongourl = mongourl + self.user + ":"+self.admin+"@"+self.ip_server[x]+":" + self.port

        if len(self.ip_server) > 1:
            mongourl = mongourl + "/" + self.database + "?authSource=admin&readPreference=primary&retryWrites=true&w=majority&replicaSet=mongo-cluster&ssl=false"
        else:
            mongourl = mongourl + "/" + self.database + "?authSource=admin&readPreference=primary&appname=MongoDB%20Compass&ssl=false"

        return mongourl
        
    def get_client(self):
        client = pymongo.MongoClient(self.getMongoURI())
        return client
