from app.api.auth import bp
from flask import jsonify, request
from app.api.auth.services import archihub_login
from flask_babel import gettext as _
# En este archivo se registran las rutas de la API para la autenticación

# Nuevo endpoint para hacer login
@bp.route('/login', methods=['POST'])
def login():
    """
    Login to obtain the access token for the document manager
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
            description: Successful login, returns access_token. If LDAP_HOST is configured, LDAP is tried first (auto-registering the user locally if they don't exist yet); if it fails or isn't configured, the local database is validated against with bcrypt
        401:
            description: Incorrect password
        404:
            description: The user does not exist
        429:
            description: Too many failed login attempts for this user (5 within the last 10 minutes); try again later
        500:
            description: Server error (unhandled exception)
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