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
    Search published resources by filters, without authentication (Elasticsearch or vector DB)
    ---
    tags:
        - Resources
    description: >
        Public route (no JWT). Only works if the "search" blueprint is registered
        (index_management.index_activation and/or .vector_activation active in the system).
        The body is delegated to app.api.search.public_services.get_resources_by_filters ->
        elasticUtils/vectorUtils, same as POST /search but without an authenticated user (post_type
        values with `viewRoles` configured are excluded by design). `post_type` is
        required.
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
                    description: "'index' (Elasticsearch, default) or 'vector'"
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
            description: Resources retrieved successfully
        500:
            description: Error retrieving resources (e.g. "post_type" missing from the body, or no active search engine)
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
    Get the blog's RSS feed (resources with viewType "blog") using the search filters
    ---
    tags:
        - Resources
    description: >
        Public route (no JWT). The filter can be passed in two ways: (1) a single `body`
        parameter with a serialized JSON object (takes priority over the others), or (2) if
        `body` isn't provided and the request has no JSON body, it's assembled from the
        individual query parameters listed below. `post_type` (or its equivalent inside `body`)
        is required; `input_filters`/`date_filters`/`location_filters`/`parents` must be
        valid JSON if sent. Internally forces `viewType: "blog"` and `full_article: true`.
    parameters:
        - in: query
          name: body
          type: string
          description: Serialized JSON with the full filter; if provided, the other query parameters are ignored
        - in: query
          name: post_type
          type: string
          description: Comma-separated slugs. Required if "body" is not used
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
          description: "'index' or 'vector'"
        - in: query
          name: page
          type: integer
        - in: query
          name: size
          type: integer
        - in: query
          name: files
          type: string
          description: "'true' to filter only resources with files"
        - in: query
          name: record_types
          type: string
          description: "JSON list or comma-separated values (alias: record_type)"
        - in: query
          name: input_filters
          type: string
          description: Serialized JSON
        - in: query
          name: date_filters
          type: string
          description: Serialized JSON
        - in: query
          name: location_filters
          type: string
          description: Serialized JSON
        - in: query
          name: parents
          type: string
          description: Serialized JSON
    produces:
        - application/rss+xml
    responses:
        200:
            description: RSS generated successfully (content-type application/rss+xml)
        400:
            description: Invalid JSON in one of the JSON parameters, non-numeric value in page/size, or missing post_type
        500:
            description: Error generating the RSS feed (e.g. no active search engine)
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