from app.api.auth import bp
from flask import jsonify, request
from flask_jwt_extended import create_access_token
from app.api.users.services import get_user

@bp.route('/login', methods=['POST'])
def login():
    """
    Login a user
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
            description: Login successful
        401:
            description: Invalid username or password
    """
    
    username = request.json.get('username')
    password = request.json.get('password')
    if username != 'test' or password != 'test':
        return jsonify({'msg': 'Invalid username or password'}), 401
    access_token = create_access_token(identity=username)
    return jsonify({'access_token': access_token}), 200