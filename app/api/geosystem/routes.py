from app.api.geosystem import bp
from flask_jwt_extended import jwt_required
from flask_jwt_extended import get_jwt_identity
from app.api.geosystem import services
from flask import request

@bp.route('/polygon', methods=['POST'])
def get_polygon():
    data = request.get_json()
    ident = data.get('ident')
    parent = data.get('parent')
    level = data.get('level')
    
    resp = services.get_shape_by_ident(ident, parent, level)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp