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
    retention = data.get('retention', 0.1)
    type = data.get('type', None)
    
    resp = services.get_shape_by_ident(ident, parent, level, type, retention)
    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp