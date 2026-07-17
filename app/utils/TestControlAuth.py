from flask import request, jsonify
from functools import wraps
from config import config
import hmac
import os
from app.utils import DatabaseHandler

mongodb = DatabaseHandler.DatabaseHandler()

TEST_MODE_MARKER_NAME = 'test_mode_active'

test_secret_key = config[os.environ['FLASK_ENV']].TEST_SECRET_HEADER_KEY


def is_disposable_instance():
    """
    The dual gate for every /health/test-control/* route (routes live in the
    same always-registered `health` blueprint as /health/live and
    /health/ready — app/api/health/routes.py — so both checks below happen
    per-request inside testControlAuthenticate, not at blueprint-registration
    time):
    1. ARCHIHUB_TEST_MODE=true — checked first, in testControlAuthenticate.
    2. A {'name': 'test_mode_active', 'value': True} document in the `system`
       Mongo collection — checked on every request here.

    This document is deliberately NEVER created by application code (no
    endpoint sets it). It must be inserted manually once, by whoever stands
    up a disposable test instance, e.g.:

        db.system.insertOne({name: 'test_mode_active', value: true})

    That split is intentional: the env var alone persists in infra config and
    is easy to leave set; if this app could set its own DB marker, restoring
    a production Mongo dump into a container that happens to have
    ARCHIHUB_TEST_MODE=true would silently become resettable. Requiring a
    separate, manual, non-automatable DB write means a real production
    restore stays protected even if the env var was left on by mistake.
    """
    marker = mongodb.get_record('system', {'name': TEST_MODE_MARKER_NAME})
    return bool(marker and marker.get('value') is True)


def testControlAuthenticate(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if os.environ.get('ARCHIHUB_TEST_MODE', '').lower() != 'true':
            return jsonify({'msg': 'Test-control API is not enabled on this instance'}), 404

        if not is_disposable_instance():
            return jsonify({'msg': 'This instance is not marked as a disposable test instance'}), 403

        # hmac.compare_digest instead of != — a plain string compare is not
        # constant-time, and this is guarding a destructive endpoint. The
        # `not test_secret_key` check comes first since compare_digest
        # requires two same-type, non-None arguments (TEST_SECRET_HEADER_KEY
        # has no insecure fallback — see config.py — so it can legitimately
        # be unset).
        secret_header = request.headers.get('X-ArchiHUB-Test-Secret')
        if not secret_header or not test_secret_key or not hmac.compare_digest(secret_header, test_secret_key):
            return jsonify({'msg': 'Invalid or missing X-ArchiHUB-Test-Secret header'}), 401

        return func(*args, **kwargs)

    return wrapper
