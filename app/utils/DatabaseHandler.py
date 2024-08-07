from . import MongoConector
import os
from dotenv import load_dotenv
load_dotenv()
# Esta clase sirve para manejar la base de datos siguiendo un esquema definido para los registros
database_name = os.environ.get('MONGO_DATABASE', 'sim-backend-prod')

class DatabaseHandler:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.database_name = database_name
            cls._instance.mongo_conector = MongoConector.MongoConector()
            cls._instance.myclient = cls._instance.mongo_conector.get_client()
            cls._instance.mydb = cls._instance.myclient[database_name]
        return cls._instance

    # Esta función sirve para obtener todos los registros de una colección
    def get_all_records(self, collection, filters={}, sort=[], limit=0, skip=0, fields={}):
        if(len(sort) > 0):
            return self.mydb[collection].find(filters, fields).sort(sort).limit(limit).skip(skip)
        else:
            return self.mydb[collection].find(filters, fields).limit(limit).skip(skip)
    
    # Esta función sirve para obtener un registro de una colección
    def get_record(self, collection, filters={}, fields={}):
        return self.mydb[collection].find_one(filters, fields)
    
    # Esta función sirve para actualizar un registro de una colección dado un filtro y un modelo de actualización. El modelo de actualización debe ser un pydantic model
    def update_record(self, collection, filters, update_model):
        return self.mydb[collection].update_one(filters, {'$set': update_model.dict(exclude_unset=True)})
    
    # Esta función sirve para ejecutar un operador de mongodb en un registro de una colección
    def update_record_operator(self, collection, filters, operator):
        return self.mydb[collection].update_one(filters, operator)
    
    # Esta función sirve para insertar un registro en una colección. El registro debe ser un pydantic model
    def insert_record(self, collection, record):
        return self.mydb[collection].insert_one(record.dict(exclude_unset=True))
    
    # Esta función sirve para incrementar un campo de un registro en una colección
    def increment_record(self, collection, filters, field, value):
        return self.mydb[collection].update_one(filters, {'$inc': {field: value}})
    
    # Esta función sirve para eliminar un registro de una colección
    def delete_record(self, collection, filters):
        return self.mydb[collection].delete_one(filters)
    
    # Esta funcion sirve para eliminar varios registros de una colección
    def delete_records(self, collection, filters):
        return self.mydb[collection].delete_many(filters)
    
    # Esta función sirve para hacer un distinct en una colección
    def distinct(self, collection, field, filters={}):
        return self.mydb[collection].distinct(field, filters)
    
    # Esta función sirve para obtener el total de registros de una colección
    def count(self, collection, filters={}):
        return self.mydb[collection].count_documents(filters)
    
    # Esta función permite hacer una agregación en una colección
    def aggregate(self, collection, pipeline):
        return self.mydb[collection].aggregate(pipeline)