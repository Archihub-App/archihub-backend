from app.api.auth import bp
from flask import jsonify, request
from app.api.auth.services import archihub_login
from flask_babel import gettext as _
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
            description: Login exitoso, retorna access_token. Si LDAP_HOST está configurado se intenta primero contra LDAP (y de no existir localmente, se registra automáticamente); si falla o no está configurado, se valida contra la base de datos local con bcrypt
        401:
            description: Contraseña incorrecta
        404:
            description: El usuario no existe
        429:
            description: Demasiados intentos fallidos de login para este usuario (5 en los últimos 10 minutos); intentar de nuevo más tarde
        500:
            description: Error en el servidor (excepción no controlada)
    """
    try:
        # Obtener username y password del request
        username = request.json.get('username')
        password = request.json.get('password')
        
        return archihub_login(username, password)
        
        
    except Exception as e:
        # archihub_login() returns its own proper (msg, status) responses for
        # every expected case (rate limit, invalid credentials, LDAP) — this
        # only catches genuinely unexpected errors (malformed request body, a
        # crash, a DB/LDAP connection failure), which shouldn't be echoed
        # verbatim to an unauthenticated caller. Log server-side instead.
        print(f"login error: {e}")
        return jsonify({'msg': _('An unexpected error occurred')}), 500