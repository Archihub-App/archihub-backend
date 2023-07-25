from mongoConector import MongoConector

# Esta clase sirve para manejar la base de datos siguiendo un esquema definido para los registros

class DatabaseHandler:
    def __init__(self, database_name):
        self.database_name = database_name
        self.mongo_conector = MongoConector(database_name)
        self.myclient = self.mongo_conector.get_client()
        self.mydb = self.myclient[database_name]

    # Esta función sirve para obtener todos los registros de una colección
    def get_all_records(self, collection, filters={}):
        return self.mydb[collection].find(filters)
    
    # Esta función sirve para obtener un registro de una colección
    def get_record(self, collection, filters={}):
        return self.mydb[collection].find_one(filters)