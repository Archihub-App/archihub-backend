from app.api.lists.services import get_by_id as get_list_by_id
from app.utils import DatabaseHandler

mongodb = DatabaseHandler.DatabaseHandler()


def get_roles():
    try:
        # Obtener el registro access_rights de la colección system
        access_rights = mongodb.get_record('system', {'name': 'access_rights'})
        # Si el registro no existe, retornar error
        if not access_rights:
            raise Exception('No existe el registro access_rights')

        roles = access_rights['data'][1]['value']

        # Obtener el listado con roles
        list = get_list_by_id(roles)

        temp = [*list['options']]
        # Agregar admin y editor a la lista
        temp.append({'id': 'admin', 'term': 'admin'})
        temp.append({'id': 'editor', 'term': 'editor'})
        temp.append({'id': 'user', 'term': 'user'})
        temp.append({'id': 'processing', 'term': 'processing'})

        return {
            'options': temp
        }

    except Exception as e:
        raise Exception(
            'Error al obtener el registro access_rights: ' + str(e))


def get_access_rights():
    try:
        # Obtener el registro access_rights de la colección system
        access_rights = mongodb.get_record('system', {'name': 'access_rights'})
        # Si el registro no existe, retornar error
        if not access_rights:
            raise Exception('No existe el registro access_rights')

        list_id = access_rights['data'][0]['value']

        # Obtener el listado con list_id
        list = get_list_by_id(list_id)

        return list

    except Exception as e:
        raise Exception('Error al obtener el registro access_rights')


def verify_role_exists(compare):
    roles = get_roles()['options']

    for role in compare:
        if role['id'] not in [r['id'] for r in roles]:
            raise Exception('El rol ' + role['id'] + ' no existe')
        
    return [role['id'] for role in roles]

def verify_accessright_exists(compare):
    access_rights = get_access_rights()['options']

    for access_right in compare:
        if access_right['id'] not in [r['id'] for r in access_rights]:
            raise Exception('El derecho de acceso ' + access_right['id'] + ' no existe')
        
    return [role['id'] for role in access_rights]