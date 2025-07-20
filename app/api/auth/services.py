import os
from flask_jwt_extended import create_access_token
from datetime import timedelta, datetime
from app.api.logs.services import register_log
from app.utils.LogActions import log_actions
from flask import jsonify, request
from app.api.users.services import get_user
from app.utils import CacheHandler
import bcrypt
from flask_babel import _
import json

LDAP_HOST = os.environ.get('LDAP_HOST', False)
LDAP_BASE_DN = os.environ.get('LDAP_BASE_DN', 'dc=example,dc=com')
LDAP_USER_DN = os.environ.get('LDAP_USER_DN', 'ou=users')
LDAP_GROUP_DN = os.environ.get('LDAP_GROUP_DN', 'ou=groups')
LDAP_TLS_CACERTFILE = os.environ.get('LDAP_TLS_CACERTFILE', None)
LDAP_TLS_REQUIRE_CERT = os.environ.get('LDAP_TLS_REQUIRE_CERT', 'demand')

cacheHandler = CacheHandler.CacheHandler()

def get_login_attempts(username):
    cache_key = f"login_attempts_{username}"
    try:
        attempts_data = cacheHandler.cache.client.get(cache_key)
        if attempts_data:
            return json.loads(attempts_data)
    except Exception as e:
        return []
    return []

def set_login_attempts(username, attempts):
    cache_key = f"login_attempts_{username}"
    try:
        cacheHandler.cache.client.setex(cache_key, 300, json.dumps(attempts))
    except:
        pass

def clear_login_attempts(username):
    cache_key = f"login_attempts_{username}"
    try:
        cacheHandler.cache.client.delete(cache_key)
    except:
        pass

def is_rate_limited(username):
    now = datetime.now()
    attempts = get_login_attempts(username)
    
    recent_attempts = [
        attempt_time for attempt_time in attempts
        if (now - datetime.fromisoformat(attempt_time)).total_seconds() < 600
    ]
    
    is_limited = len(recent_attempts) >= 5
    
    if len(recent_attempts) != len(attempts):
        set_login_attempts(username, recent_attempts)
    
    return is_limited

def record_login_attempt(username):
    now = datetime.now()
    attempts = get_login_attempts(username)
    attempts.append(now.isoformat())
    set_login_attempts(username, attempts)

def ldap_login(username, password):
    try:
        import ldap
        # Initialize LDAP connection
        ldap_client = ldap.initialize(LDAP_HOST)

        # Set TLS options for LDAPS
        if LDAP_HOST and LDAP_HOST.lower().startswith('ldaps://'):
            ldap_client.set_option(ldap.OPT_X_TLS_NEWCTX, 0)
            if LDAP_TLS_CACERTFILE:
                ldap_client.set_option(ldap.OPT_X_TLS_CACERTFILE, LDAP_TLS_CACERTFILE)

            if LDAP_TLS_REQUIRE_CERT:
                cert_options = {
                    'never': ldap.OPT_X_TLS_NEVER,
                    'allow': ldap.OPT_X_TLS_ALLOW,
                    'try': ldap.OPT_X_TLS_TRY,
                    'demand': ldap.OPT_X_TLS_DEMAND
                }
                ldap_client.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, cert_options.get(LDAP_TLS_REQUIRE_CERT.lower(), ldap.OPT_X_TLS_DEMAND))

        ldap_client.set_option(ldap.OPT_REFERRALS, 0)
        
        # Create user DN
        user_dn = f"uid={username},{LDAP_USER_DN},{LDAP_BASE_DN}"
        
        # Attempt to bind with user credentials
        ldap_client.simple_bind_s(user_dn, password)
        
        # Search for user attributes
        search_filter = f"(uid={username})"
        result = ldap_client.search_s(
            LDAP_BASE_DN,
            ldap.SCOPE_SUBTREE,
            search_filter,
            ['cn', 'mail']
        )

        if not result:
            return None
            
        # Get user details from LDAP
        user_attributes = result[0][1]
        
        # Create or update local user
        user = {
            'username': user_attributes.get('mail', [b''])[0].decode('utf-8'),
            'name': user_attributes.get('cn', [b''])[0].decode('utf-8'),
            'password': '',
            'loginType': 'ldap',
            'roles': ['user'],
            'accessRights': [],
        }
        
        from app.api.users.services import get_user, register_user
        local_user = get_user(user['username'])
        if not local_user:
            newuser, status = register_user(user)
            if status != 201:
                return None
        else:
            user = local_user
        
        return user
        
    except ldap.INVALID_CREDENTIALS:
        return None
    except Exception as e:
        print(f"LDAP Error: {str(e)}")
        return None
    finally:
        try:
            ldap_client.unbind()
        except:
            pass
        
def archihub_login(username, password):
    if is_rate_limited(username):
        attempts = get_login_attempts(username)
        register_log(username, log_actions.get('user_login_blocked', 'LOGIN_BLOCKED'), {
            'reason': 'rate_limited',
            'attempts': len(attempts)
        })
        return jsonify({'msg': _('Too many login attempts. Please try again in 10 minutes.')}), 429
    
    expires_delta = timedelta(days=1)
    
    if LDAP_HOST:
        ldap_user = ldap_login(username, password)
        if ldap_user:
            clear_login_attempts(username)  # Clear attempts on successful login
            access_token = create_access_token(identity=username, expires_delta=expires_delta)
            register_log(username, log_actions['user_login'], {})
            
            return jsonify({'access_token': access_token}), 200
        else:
            record_login_attempt(username)  # Record failed LDAP attempt
        
    user = get_user(username)
    
    if not user:
        record_login_attempt(username)  # Record failed attempt
        return jsonify({'msg': _('User not found')}), 404
    
    if not bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
        record_login_attempt(username)  # Record failed attempt
        return jsonify({'msg': _('Invalid password')}), 401
    
    clear_login_attempts(username)  # Clear attempts on successful login
    access_token = create_access_token(identity=username, expires_delta=expires_delta)
    
    register_log(username, log_actions['user_login'], {})
    
    return jsonify({'access_token': access_token}), 200