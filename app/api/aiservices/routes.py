from app.api.aiservices import bp
from flask import jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.api.aiservices import services
from app.api.users import services as user_services
from flask_babel import _

@bp.route('', methods=['GET'])
@jwt_required()
def get_llm_models():
    """
    List the configured AI models/services (without exposing the API key)
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    responses:
        200:
            description: Array of models configured in the "llm_models" collection (the "key" field is excluded)
        401:
            description: You don't have permission to perform this action (requires admin, processing, or llm role)
        500:
            description: Error retrieving the models
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing') and not user_services.has_role(current_user, 'llm'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    llm_models = services.get_llm_models()
    if isinstance(llm_models, list):
        return tuple(llm_models)
    else:
        return llm_models

@bp.route('/providers', methods=['GET'])
@jwt_required()
def get_llm_providers():
    """
    List the supported AI providers (OpenAI, Google, OpenRouter, Azure, Ollama, LlamaServer)
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    responses:
        200:
            description: Array/object with the supported providers
        401:
            description: You don't have permission to perform this action (requires admin, processing, or llm role)
        500:
            description: Error retrieving the providers
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing') and not user_services.has_role(current_user, 'llm'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    llm_providers = services.get_llm_providers()
    if isinstance(llm_providers, list):
        return tuple(llm_providers)
    else:
        return llm_providers

@bp.route('', methods=['POST'])
@jwt_required()
def create_llm_model():
    """
    Register a new AI model/service
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
                    description: Name of the model/service (must be unique).
                provider:
                    type: string
                    description: "One of the supported providers: OpenAI, Google, OpenRouter, Azure, Ollama, LlamaServer."
                key:
                    type: string
                    description: API key for the provider. Encrypted with Fernet before being saved.
                endpoint:
                    type: string
                endpointCognitive:
                    type: string
            required:
                - name
                - provider
                - key
    responses:
        201:
            description: Model created successfully
        400:
            description: A model with that name already exists
        404:
            description: The specified provider is not in the list of supported providers
        500:
            description: Error creating the model (includes the case where a required field is missing from the body)
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    data = request.get_json()
    llm_model = services.create_llm_model(data)
    return llm_model


@bp.route('/skills', methods=['GET'])
@jwt_required()
def list_skills():
    """
    List the available skills/agents (SkillManager), with search and an optional tree view
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    parameters:
        - in: query
          name: query
          type: string
          required: false
          description: Search text (alias "q" is also accepted).
        - in: query
          name: q
          type: string
          required: false
          description: Alias for "query".
        - in: query
          name: include_content
          type: boolean
          required: false
          description: If true, includes the full content of each skill.
        - in: query
          name: tree
          type: boolean
          required: false
          description: If true, returns the skills organized as a folder tree.
    responses:
        200:
            description: List (or tree) of available skills
        401:
            description: You don't have permission to perform this action (requires admin, processing, or llm role)
        500:
            description: Error listing the skills
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing') and not user_services.has_role(current_user, 'llm'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    skills = services.list_skills(
        query=request.args.get('query') or request.args.get('q'),
        include_content=request.args.get('include_content'),
        tree=request.args.get('tree'),
    )
    return skills


@bp.route('/skills/sync', methods=['POST'])
@jwt_required()
def sync_skills():
    """
    Sync the skills catalog from the filesystem
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    responses:
        200:
            description: '{"skills": [...], "count": <number of skills synced>}'
        401:
            description: You don't have permission to perform this action (requires admin or processing role)
        500:
            description: Error syncing the skills
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    return services.sync_skills()


@bp.route('/skills', methods=['POST'])
@jwt_required()
def create_skill():
    """
    Create a new skill
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                path:
                    type: string
                    description: Path/identifier of the new skill (alias "id" is also accepted).
                id:
                    type: string
                    description: Alias for "path".
                content:
                    type: string
                    description: Content of the skill.
    responses:
        201:
            description: Skill created successfully
        401:
            description: You don't have permission to perform this action (requires admin or processing role)
        500:
            description: Error creating the skill
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    data = request.get_json() or {}
    return services.save_skill(None, data)


@bp.route('/skills/<path:skill_path>', methods=['GET'])
@jwt_required()
def get_skill(skill_path):
    """
    Get a skill by its path, including its content
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    parameters:
        - in: path
          name: skill_path
          type: string
          required: true
          description: Path of the skill (may include subfolders, e.g. "folder/skill").
    responses:
        200:
            description: Skill found, including its content
        401:
            description: You don't have permission to perform this action (requires admin, processing, or llm role)
        404:
            description: Skill not found
        500:
            description: Error retrieving the skill
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing') and not user_services.has_role(current_user, 'llm'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    return services.get_skill(skill_path)


@bp.route('/skills/<path:skill_path>', methods=['PUT'])
@jwt_required()
def update_skill(skill_path):
    """
    Update an existing skill by its path
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    parameters:
        - in: path
          name: skill_path
          type: string
          required: true
          description: Path of the skill to update.
        - in: body
          name: body
          schema:
            type: object
            properties:
                content:
                    type: string
                    description: New content for the skill.
    responses:
        200:
            description: Skill updated successfully
        401:
            description: You don't have permission to perform this action (requires admin or processing role)
        500:
            description: Error updating the skill
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    data = request.get_json() or {}
    return services.save_skill(skill_path, data)


@bp.route('/skills/<path:skill_path>', methods=['DELETE'])
@jwt_required()
def delete_skill(skill_path):
    """
    Delete a skill by its path
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    parameters:
        - in: path
          name: skill_path
          type: string
          required: true
          description: Path of the skill to delete.
    responses:
        200:
            description: Skill deleted successfully
        401:
            description: You don't have permission to perform this action (requires admin or processing role)
        404:
            description: Skill not found
        500:
            description: Error deleting the skill
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    return services.delete_skill(skill_path)

@bp.route('/<model_id>', methods=['PUT'])
@jwt_required()
def update_llm_model(model_id):
    """
    Update an existing AI model/service
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    parameters:
        - in: path
          name: model_id
          type: string
          required: true
          description: Id of the model to update.
        - in: body
          name: body
          schema:
            type: object
            description: >
                All fields are optional. If the stored provider is not "Azure",
                "endpoint"/"endpointCognitive" are discarded from the payload even if sent.
            properties:
                name:
                    type: string
                key:
                    type: string
                    description: New API key (encrypted with Fernet before being saved).
                endpoint:
                    type: string
                endpointCognitive:
                    type: string
    responses:
        200:
            description: Model updated successfully
        500:
            description: Error updating the model (includes invalid id or nonexistent model)
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    data = request.get_json()
    llm_model = services.update_llm_model(model_id, data)
    return llm_model

@bp.route('/<model_id>', methods=['DELETE'])
@jwt_required()
def delete_llm_model(model_id):
    """
    Delete an AI model/service
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    parameters:
        - in: path
          name: model_id
          type: string
          required: true
          description: Id of the model to delete.
    responses:
        200:
            description: Model deleted successfully (does not check beforehand whether the model exists)
        401:
            description: You don't have permission to perform this action (requires admin or processing role)
        500:
            description: Error deleting the model (e.g. invalid id format)
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    llm_model = services.delete_llm_model(model_id)
    return llm_model

@bp.route('/model/<id>', methods=['GET'])
@jwt_required()
def get_llm_model(id):
    """
    Get an AI model/service by its id (without exposing the API key)
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: Id of the model.
    responses:
        200:
            description: Model found (the "key" field is excluded)
        401:
            description: You don't have permission to perform this action (requires admin, processing, or llm role)
        404:
            description: Model not found
        500:
            description: Error retrieving the model
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing') and not user_services.has_role(current_user, 'llm'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    model = services.get_llm_model(id)
    return model

@bp.route('/models/<id>', methods=['GET'])
@jwt_required()
def get_provider_models(id):
    """
    Get the list of models available from the provider configured for an AI model/service
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: Id of the registered AI model/service (defines which provider and credentials to use).
    responses:
        200:
            description: List of models exposed by the provider (live call to the AI provider)
        500:
            description: >
                Error querying the provider (model not found, unsupported provider, or an error from
                the provider's API)
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing') and not user_services.has_role(current_user, 'llm'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    models = services.get_provider_models(id)
    return models

@bp.route('/conversation', methods=['POST'])
@jwt_required()
def set_conversation():
    """
    Send a message/conversation turn to an AI assistant (transcription, document, image gallery, or atlas)
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                type:
                    type: string
                    description: "Conversation type: transcription, document, image_gallery, or atlas (atlas requires the capability of the same name to be enabled in the system configuration)."
                provider:
                    type: object
                    description: 'Object with at least "id" (id of the AI model/service to use).'
            required:
                - type
                - provider
            description: >
                The remaining required fields vary depending on "type" (e.g. record_id, messages, images) and
                are validated/transformed by SkillManager.prepare_conversation_payload before processing.
    responses:
        200:
            description: Response from the AI assistant (shape depends on "type"; may include streaming)
        500:
            description: >
                Error processing the conversation (includes an unrecognized "type" or invalid payload, which
                result in an empty response with status 200 and no body, due to how this view returns)
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing') and not user_services.has_role(current_user, 'llm'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    data = request.get_json()
    llm_model = services.set_conversation(data, current_user)
    return llm_model

@bp.route('/conversation/<id>', methods=['GET'])
@jwt_required()
def get_conversation(id):
    """
    Get an AI conversation by its id (only for the authenticated user)
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: Id of the conversation.
    responses:
        200:
            description: >
                Conversation found (_id, created_at, updated_at, resource_id, page, messages). Images
                referenced in the messages are converted to base64 on the fly.
        401:
            description: You don't have permission to perform this action (requires admin, processing, or llm role)
        404:
            description: Conversation not found (or does not belong to the authenticated user)
        500:
            description: Error retrieving the conversation
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing') and not user_services.has_role(current_user, 'llm'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    conversation = services.get_conversation(id, current_user)
    return conversation

@bp.route('/conversation/<id>', methods=['DELETE'])
@jwt_required()
def delete_conversation(id):
    """
    Delete an AI conversation by its id (only for the authenticated user)
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: Id of the conversation.
    responses:
        200:
            description: Conversation deleted successfully
        400:
            description: The conversation id is not a valid ObjectId format
        401:
            description: You don't have permission to perform this action (requires admin, processing, or llm role)
        404:
            description: Conversation not found (or does not belong to the authenticated user)
        500:
            description: Error deleting the conversation
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing') and not user_services.has_role(current_user, 'llm'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    conversation = services.delete_conversation(id, current_user)
    return conversation

@bp.route('/conversation/history', methods=['POST'])
@jwt_required()
def get_conversation_history():
    """
    Get the conversation history (first message of each) for a record, image gallery, or the atlas assistant
    ---
    security:
        - JWT: []
    tags:
        - AI Services
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                type:
                    type: string
                    description: "record, transcription, document, image_gallery, or atlas."
                id:
                    type: string
                    description: Id of the record or resource (resource_id), depending on "type". Not applicable for type=atlas.
                processing_slug:
                    type: string
                    description: Optional filter by processing slug (alias "slug" is also accepted). Applies to transcription/document/atlas.
    responses:
        200:
            description: >
                Array of conversations (_id, created_at, updated_at, type, processing_slug, and "messages"
                trimmed to the first message of each conversation). If the referenced record doesn't exist or
                isn't accessible, or if "type" doesn't match any supported case, returns 500 or [].
        500:
            description: Error retrieving the history (includes an inaccessible/nonexistent record for record/transcription/document)
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing') and not user_services.has_role(current_user, 'llm'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    data = request.get_json()
    history = services.get_conversation_history(data, current_user)
    return history
