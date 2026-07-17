from app.api.search import bp
from app.api.search import public_services
from flask import request, jsonify
import os
import json


def _parse_record_types_arg(value):
    if value is None:
        return None

    try:
        parsed_value = json.loads(value)
    except json.JSONDecodeError:
        parsed_value = value

    if isinstance(parsed_value, str):
        return [item.strip() for item in parsed_value.split(',') if item.strip()]

    if isinstance(parsed_value, list):
        return parsed_value

    return None

@bp.route('/public', methods=['POST'])
def get_all_public():
    """
    Buscar recursos publicados por filtros, sin autenticación (Elasticsearch o vector DB)
    ---
    tags:
        - Recursos
    description: >
        Ruta pública (sin JWT). Solo funciona si el blueprint "search" está registrado
        (index_management.index_activation y/o .vector_activation activos en el sistema).
        El body se delega en app.api.search.public_services.get_resources_by_filters ->
        elasticUtils/vectorUtils, igual que POST /search pero sin usuario autenticado (los
        post_type con `viewRoles` configurados quedan excluidos por diseño). `post_type` es
        obligatorio.
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                post_type:
                    type: array
                    items:
                        type: string
                keyword:
                    type: string
                searchSource:
                    type: string
                    description: "'index' (Elasticsearch, por defecto) o 'vector'"
                sortBy:
                    type: string
                sortOrder:
                    type: string
                activeColumns:
                    type: array
                    items:
                        type: object
                size:
                    type: integer
            required:
                - post_type
    responses:
        200:
            description: Resources obtenidos exitosamente
        500:
            description: Error al obtener los resources (p.ej. falta "post_type" en el body, o no hay motor de búsqueda activo)
    """
    body = request.json
    resp = public_services.get_resources_by_filters(body)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp


@bp.route('/public/rss', methods=['GET'])
def get_blog_rss_public():
    """
    Obtener el feed RSS del blog (recursos con viewType "blog") usando los filtros de búsqueda
    ---
    tags:
        - Recursos
    description: >
        Ruta pública (sin JWT). El filtro se puede pasar de dos formas: (1) un único parámetro
        `body` con un objeto JSON serializado (tiene prioridad sobre los demás), o (2) si
        `body` no viene y la request no trae un JSON body, se arma a partir de los parámetros
        de query individuales listados abajo. `post_type` (o el equivalente dentro de `body`)
        es obligatorio; `input_filters`/`date_filters`/`location_filters`/`parents` deben ser
        JSON válido si se envían. Internamente fuerza `viewType: "blog"` y `full_article: true`.
    parameters:
        - in: query
          name: body
          type: string
          description: JSON serializado con el filtro completo; si viene, ignora los demás parámetros de query
        - in: query
          name: post_type
          type: string
          description: Slugs separados por coma. Obligatorio si no se usa "body"
        - in: query
          name: keyword
          type: string
        - in: query
          name: sortBy
          type: string
        - in: query
          name: sortOrder
          type: string
        - in: query
          name: searchSource
          type: string
          description: "'index' o 'vector'"
        - in: query
          name: page
          type: integer
        - in: query
          name: size
          type: integer
        - in: query
          name: files
          type: string
          description: "'true' para filtrar solo recursos con archivos"
        - in: query
          name: record_types
          type: string
          description: "Lista JSON o valores separados por coma (alias: record_type)"
        - in: query
          name: input_filters
          type: string
          description: JSON serializado
        - in: query
          name: date_filters
          type: string
          description: JSON serializado
        - in: query
          name: location_filters
          type: string
          description: JSON serializado
        - in: query
          name: parents
          type: string
          description: JSON serializado
    produces:
        - application/rss+xml
    responses:
        200:
            description: RSS generado exitosamente (content-type application/rss+xml)
        400:
            description: JSON inválido en alguno de los parámetros JSON, valor no numérico en page/size, o falta post_type
        500:
            description: Error al generar el RSS (p.ej. no hay motor de búsqueda activo)
    """
    body = {}
    body_param = request.args.get('body')

    if body_param:
        try:
            body = json.loads(body_param)
        except json.JSONDecodeError:
            return jsonify({'msg': 'Invalid body JSON'}), 400
    elif request.is_json:
        body = request.get_json(silent=True) or {}
    else:
        post_types = request.args.get('post_type')
        if post_types:
            body['post_type'] = [p.strip() for p in post_types.split(',') if p.strip()]

        if request.args.get('keyword'):
            body['keyword'] = request.args.get('keyword')

        if request.args.get('sortBy'):
            body['sortBy'] = request.args.get('sortBy')

        if request.args.get('sortOrder'):
            body['sortOrder'] = request.args.get('sortOrder')

        if request.args.get('searchSource'):
            body['searchSource'] = request.args.get('searchSource')

        if request.args.get('page'):
            try:
                body['page'] = int(request.args.get('page'))
            except ValueError:
                return jsonify({'msg': 'Invalid page value'}), 400

        if request.args.get('size'):
            try:
                body['size'] = int(request.args.get('size'))
            except ValueError:
                return jsonify({'msg': 'Invalid size value'}), 400

        if request.args.get('files'):
            body['files'] = request.args.get('files') == 'true'

        record_types = _parse_record_types_arg(request.args.get('record_types'))
        if record_types is None:
            record_types = _parse_record_types_arg(request.args.get('record_type'))
        if record_types is not None:
            body['record_types'] = record_types

        for json_key in ['input_filters', 'date_filters', 'location_filters', 'parents']:
            if request.args.get(json_key):
                try:
                    body[json_key] = json.loads(request.args.get(json_key))
                except json.JSONDecodeError:
                    return jsonify({'msg': f'Invalid {json_key} JSON'}), 400

    if 'post_type' not in body:
        return jsonify({'msg': 'post_type is required'}), 400

    body['viewType'] = 'blog'
    body['full_article'] = True

    base_url = os.environ.get('RSS_BASE_URL', 'https://archihub.bit-sol.com.co')
    link_template = os.environ.get('RSS_LINK_TEMPLATE', '/detail/{id}')
    feed_title = os.environ.get('RSS_FEED_TITLE', 'ArchiHUB Blog')
    feed_description = os.environ.get('RSS_FEED_DESCRIPTION', 'ArchiHUB Blog feed')

    resp = public_services.get_rss_feed(body, base_url, link_template, feed_title, feed_description)

    if isinstance(resp, list):
        return tuple(resp)
    else:
        return resp