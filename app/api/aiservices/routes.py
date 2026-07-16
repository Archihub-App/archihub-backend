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
    Listar los modelos/servicios de IA configurados (sin exponer la API key)
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    responses:
        200:
            description: Arreglo de modelos configurados en la colección "llm_models" (campo "key" excluido)
        401:
            description: No tienes permisos para realizar esta acción (requiere rol admin, processing o llm)
        500:
            description: Error al obtener los modelos
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
    Listar los proveedores de IA soportados (OpenAI, Google, OpenRouter, Azure, Ollama, LlamaServer)
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    responses:
        200:
            description: Arreglo/objeto con los proveedores soportados
        401:
            description: No tienes permisos para realizar esta acción (requiere rol admin, processing o llm)
        500:
            description: Error al obtener los proveedores
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
    Registrar un nuevo modelo/servicio de IA
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                name:
                    type: string
                    description: Nombre del modelo/servicio (debe ser único).
                provider:
                    type: string
                    description: "Uno de los proveedores soportados: OpenAI, Google, OpenRouter, Azure, Ollama, LlamaServer."
                key:
                    type: string
                    description: API key del proveedor. Se cifra con Fernet antes de guardarse.
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
            description: Modelo creado exitosamente
        400:
            description: Ya existe un modelo con ese nombre
        404:
            description: El proveedor indicado no está en la lista de proveedores soportados
        500:
            description: Error al crear el modelo (incluye el caso en que falte algún campo requerido en el body)
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
    Listar los skills/agentes disponibles (SkillManager), con búsqueda y vista opcional en árbol
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    parameters:
        - in: query
          name: query
          type: string
          required: false
          description: Texto de búsqueda (alias "q" también aceptado).
        - in: query
          name: q
          type: string
          required: false
          description: Alias de "query".
        - in: query
          name: include_content
          type: boolean
          required: false
          description: Si es verdadero, incluye el contenido completo de cada skill.
        - in: query
          name: tree
          type: boolean
          required: false
          description: Si es verdadero, retorna los skills organizados en árbol de carpetas.
    responses:
        200:
            description: Listado (o árbol) de skills disponibles
        401:
            description: No tienes permisos para realizar esta acción (requiere rol admin, processing o llm)
        500:
            description: Error al listar los skills
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
    Sincronizar el catálogo de skills desde el sistema de archivos
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    responses:
        200:
            description: '{"skills": [...], "count": <número de skills sincronizados>}'
        401:
            description: No tienes permisos para realizar esta acción (requiere rol admin o processing)
        500:
            description: Error al sincronizar los skills
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    return services.sync_skills()


@bp.route('/skills', methods=['POST'])
@jwt_required()
def create_skill():
    """
    Crear un nuevo skill
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                path:
                    type: string
                    description: Ruta/identificador del nuevo skill (alias "id" también aceptado).
                id:
                    type: string
                    description: Alias de "path".
                content:
                    type: string
                    description: Contenido del skill.
    responses:
        201:
            description: Skill creado exitosamente
        401:
            description: No tienes permisos para realizar esta acción (requiere rol admin o processing)
        500:
            description: Error al crear el skill
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
    Obtener un skill por su ruta, incluyendo su contenido
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    parameters:
        - in: path
          name: skill_path
          type: string
          required: true
          description: Ruta del skill (puede incluir subcarpetas, p. ej. "carpeta/skill").
    responses:
        200:
            description: Skill encontrado, con su contenido
        401:
            description: No tienes permisos para realizar esta acción (requiere rol admin, processing o llm)
        404:
            description: Skill no encontrado
        500:
            description: Error al obtener el skill
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing') and not user_services.has_role(current_user, 'llm'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    return services.get_skill(skill_path)


@bp.route('/skills/<path:skill_path>', methods=['PUT'])
@jwt_required()
def update_skill(skill_path):
    """
    Actualizar un skill existente por su ruta
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    parameters:
        - in: path
          name: skill_path
          type: string
          required: true
          description: Ruta del skill a actualizar.
        - in: body
          name: body
          schema:
            type: object
            properties:
                content:
                    type: string
                    description: Nuevo contenido del skill.
    responses:
        200:
            description: Skill actualizado exitosamente
        401:
            description: No tienes permisos para realizar esta acción (requiere rol admin o processing)
        500:
            description: Error al actualizar el skill
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
    Eliminar un skill por su ruta
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    parameters:
        - in: path
          name: skill_path
          type: string
          required: true
          description: Ruta del skill a eliminar.
    responses:
        200:
            description: Skill eliminado exitosamente
        401:
            description: No tienes permisos para realizar esta acción (requiere rol admin o processing)
        404:
            description: Skill no encontrado
        500:
            description: Error al eliminar el skill
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    return services.delete_skill(skill_path)

@bp.route('/<model_id>', methods=['PUT'])
@jwt_required()
def update_llm_model(model_id):
    """
    Actualizar un modelo/servicio de IA existente
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    parameters:
        - in: path
          name: model_id
          type: string
          required: true
          description: Id del modelo a actualizar.
        - in: body
          name: body
          schema:
            type: object
            description: >
                Todos los campos son opcionales. Si el provider guardado no es "Azure",
                "endpoint"/"endpointCognitive" se descartan del payload aunque se envíen.
            properties:
                name:
                    type: string
                key:
                    type: string
                    description: Nueva API key (se cifra con Fernet antes de guardarse).
                endpoint:
                    type: string
                endpointCognitive:
                    type: string
    responses:
        200:
            description: Modelo actualizado exitosamente
        500:
            description: Error al actualizar el modelo (incluye id inválido o modelo inexistente)
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
    Eliminar un modelo/servicio de IA
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    parameters:
        - in: path
          name: model_id
          type: string
          required: true
          description: Id del modelo a eliminar.
    responses:
        200:
            description: Modelo eliminado exitosamente (no valida previamente que el modelo exista)
        401:
            description: No tienes permisos para realizar esta acción (requiere rol admin o processing)
        500:
            description: Error al eliminar el modelo (p. ej. id con formato inválido)
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
    Obtener un modelo/servicio de IA por su id (sin exponer la API key)
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: Id del modelo.
    responses:
        200:
            description: Modelo encontrado (campo "key" excluido)
        401:
            description: No tienes permisos para realizar esta acción (requiere rol admin, processing o llm)
        404:
            description: Modelo no encontrado
        500:
            description: Error al obtener el modelo
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
    Obtener la lista de modelos disponibles en el proveedor configurado por un modelo/servicio de IA
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: Id del modelo/servicio de IA registrado (define qué proveedor y credenciales usar).
    responses:
        200:
            description: Lista de modelos que expone el proveedor (llamada en vivo al proveedor de IA)
        500:
            description: >
                Error al consultar el proveedor (modelo no encontrado, proveedor no soportado, o error de
                la API del proveedor)
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
    Enviar un mensaje/turno de conversación a un asistente de IA (transcripción, documento, galería de imágenes o atlas)
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                type:
                    type: string
                    description: "Tipo de conversación: transcription, document, image_gallery o atlas (atlas requiere la capability del mismo nombre habilitada en la configuración del sistema)."
                provider:
                    type: object
                    description: 'Objeto con al menos "id" (id del modelo/servicio de IA a usar).'
            required:
                - type
                - provider
            description: >
                El resto de campos requeridos varían según "type" (p. ej. record_id, mensajes, imágenes) y
                son validados/transformados por SkillManager.prepare_conversation_payload antes de procesarse.
    responses:
        200:
            description: Respuesta del asistente de IA (forma depende de "type"; puede incluir streaming)
        500:
            description: >
                Error procesando la conversación (incluye "type" no reconocido o payload inválido, que
                resultan en una respuesta vacía con status 200 sin cuerpo por cómo retorna esta vista)
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
    Obtener una conversación de IA por su id (solo del usuario autenticado)
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: Id de la conversación.
    responses:
        200:
            description: >
                Conversación encontrada (_id, created_at, updated_at, resource_id, page, messages). Las
                imágenes referenciadas en los mensajes se convierten a base64 al vuelo.
        401:
            description: No tienes permisos para realizar esta acción (requiere rol admin, processing o llm)
        404:
            description: Conversación no encontrada (o no pertenece al usuario autenticado)
        500:
            description: Error al obtener la conversación
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
    Eliminar una conversación de IA por su id (solo del usuario autenticado)
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    parameters:
        - in: path
          name: id
          type: string
          required: true
          description: Id de la conversación.
    responses:
        200:
            description: Conversación eliminada exitosamente
        400:
            description: El id de conversación no tiene un formato válido de ObjectId
        401:
            description: No tienes permisos para realizar esta acción (requiere rol admin, processing o llm)
        404:
            description: Conversación no encontrada (o no pertenece al usuario autenticado)
        500:
            description: Error al eliminar la conversación
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
    Obtener el historial de conversaciones (primer mensaje de cada una) para un record, galería de imágenes o el asistente atlas
    ---
    security:
        - JWT: []
    tags:
        - Servicios de IA
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                type:
                    type: string
                    description: "record, transcription, document, image_gallery o atlas."
                id:
                    type: string
                    description: Id del record o del recurso (resource_id), según "type". No aplica para type=atlas.
                processing_slug:
                    type: string
                    description: Filtro opcional por slug de procesamiento (alias "slug" también aceptado). Aplica a transcription/document/atlas.
    responses:
        200:
            description: >
                Arreglo de conversaciones (_id, created_at, updated_at, type, processing_slug, y "messages"
                recortado al primer mensaje de cada conversación). Si el record referenciado no existe o no
                es accesible, o si "type" no coincide con ninguno de los casos soportados, retorna 500 o [].
        500:
            description: Error al obtener el historial (incluye record inaccesible/inexistente para record/transcription/document)
    """
    current_user = get_jwt_identity()

    if not user_services.has_role(current_user, 'admin') and not user_services.has_role(current_user, 'processing') and not user_services.has_role(current_user, 'llm'):
        return jsonify({'msg': _('You don\'t have the required authorization')}), 401

    data = request.get_json()
    history = services.get_conversation_history(data, current_user)
    return history