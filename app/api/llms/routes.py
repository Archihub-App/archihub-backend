from app.api.llms import bp
from flask import jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.api.llms import services
from app.api.users import services as user_services
from flask_babel import _

@bp.route('', methods=['GET'])
@jwt_required()
def get_llm_models():
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') or not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    llm_models = services.get_llm_models()
    print(llm_models)
    if isinstance(llm_models, list):
        return tuple(llm_models)
    else:
        return llm_models
    
@bp.route('/providers', methods=['GET'])
@jwt_required()
def get_llm_providers():
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') or not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    llm_providers = services.get_llm_providers()
    if isinstance(llm_providers, list):
        return tuple(llm_providers)
    else:
        return llm_providers
    
@bp.route('', methods=['POST'])
@jwt_required()
def create_llm_model():
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') or not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    data = request.get_json()
    llm_model = services.create_llm_model(data)
    return llm_model

@bp.route('/<model_id>', methods=['DELETE'])
@jwt_required()
def delete_llm_model(model_id):
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') or not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    llm_model = services.delete_llm_model(model_id)
    return llm_model

@bp.route('/<model_id>', methods=['PUT'])
@jwt_required()
def update_llm_model(model_id):
    current_user = get_jwt_identity()
    
    if not user_services.has_role(current_user, 'admin') or not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    data = request.get_json()
    llm_model = services.update_llm_model(model_id, data)
    return llm_model

@bp.route('/models/<id>', methods=['GET'])
@jwt_required()
def get_provider_models(id):
    current_user = get_jwt_identity()

    models = services.get_provider_models(id)
    return models

@bp.route('/conversation', methods=['POST'])
@jwt_required()
def set_conversation():
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') or not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    data = request.get_json()
    llm_model = services.set_conversation(data, current_user)
    return llm_model