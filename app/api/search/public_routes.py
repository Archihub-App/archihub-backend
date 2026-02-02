from app.api.search import bp
from app.api.search import public_services
from flask import request, jsonify
import os
import json

@bp.route('/public', methods=['POST'])
def get_all_public():
    """
    Obtener todos los resources dado un body de filtros
    ---
    tags:
        - Recursos
    parameters:
        - in: body
          name: body
          schema:
            type: object
            properties:
                filters:
                    type: object
                sort:
                    type: string
                limit:
                    type: integer
                skip:
                    type: integer
    responses:
        200:
            description: Resources obtenidos exitosamente
        401:
            description: No tiene permisos para obtener los resources
        500:
            description: Error al obtener los resources
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
    Obtener feed RSS del blog usando los filtros de busqueda
    ---
    tags:
        - Recursos
    parameters:
        - in: query
          name: post_type
          type: string
        - in: query
          name: keyword
          type: string
        - in: query
          name: status
          type: string
        - in: query
          name: page
          type: integer
        - in: query
          name: size
          type: integer
        - in: query
          name: body
          type: string
        - in: query
          name: base_url
          type: string
        - in: query
          name: link_template
          type: string
        - in: query
          name: feed_title
          type: string
        - in: query
          name: feed_description
          type: string
    responses:
        200:
            description: RSS generado exitosamente
        400:
            description: Filtros invalidos
        500:
            description: Error al generar RSS
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

        if request.args.get('status'):
            body['status'] = request.args.get('status')

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