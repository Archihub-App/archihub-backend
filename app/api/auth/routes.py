from app.api.auth import bp
from flask import jsonify, request
from app.api.auth.services import archihub_login
# En este archivo se registran las rutas de la API para la autenticación

# Nuevo endpoint para hacer login
@bp.route('/login', methods=['POST'])
def login():
    """
    Login para obtener el token de acceso al gestor documental
    ---
    tags:
        - Auth
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                username:
                    type: string
                password:
                    type: string
            required:
                - username
                - password
    responses:
        200:
            description: Login exitoso
        401:
            description: Usuario o contraseña inválidos
        500:
            description: Error en el servidor
    """
    try:
        # Obtener username y password del request
        username = request.json.get('username')
        password = request.json.get('password')
        
        return archihub_login(username, password)
        
        
    except Exception as e:
        return jsonify({'msg': str(e)}), 500