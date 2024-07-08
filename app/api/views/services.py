from app.utils import DatabaseHandler

mongodb = DatabaseHandler.DatabaseHandler()

def create(body, user):
    try:
        print(body)

        return {'msg': 'Vista de consulta creada exitosamente'}, 201
    except Exception as e:
        return {'msg': str(e)}, 500