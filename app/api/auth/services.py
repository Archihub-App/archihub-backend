import os
from flask_jwt_extended import create_access_token
from datetime import timedelta
from app.api.logs.services import register_log
from app.utils.LogActions import log_actions
from flask import jsonify, request
from app.api.users.services import get_user
import bcrypt
from flask_babel import _

LDAP_HOST = os.environ.get('LDAP_HOST', False)
LDAP_BASE_DN = os.environ.get('LDAP_BASE_DN', 'dc=example,dc=com')
LDAP_USER_DN = os.environ.get('LDAP_USER_DN', 'ou=users')
LDAP_GROUP_DN = os.environ.get('LDAP_GROUP_DN', 'ou=groups')
LDAP_TLS_CACERTFILE = os.environ.get('LDAP_TLS_CACERTFILE', None)
LDAP_TLS_REQUIRE_CERT = os.environ.get('LDAP_TLS_REQUIRE_CERT', None)

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
    expires_delta = timedelta(days=1)
    if LDAP_HOST:
        ldap_user = ldap_login(username, password)
        if ldap_user:
            access_token = create_access_token(identity=username, expires_delta=expires_delta)
            register_log(username, log_actions['user_login'], {})
            
            return jsonify({'access_token': access_token}), 200
        
    user = get_user(username)
    
    if not user:
        return jsonify({'msg': _('User not found')}), 404
    if not bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
        return jsonify({'msg': _('Invalid password')}), 401
    
    access_token = create_access_token(identity=username, expires_delta=expires_delta)
    
    register_log(username, log_actions['user_login'], {})
    
    return jsonify({'access_token': access_token}), 200
            