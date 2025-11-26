"""
Cloudflare Access Authentication Middleware
"""
import jwt
import requests
from functools import wraps
from flask import request, jsonify, current_app
import os

# Cloudflare Access configuration
CF_TEAM_DOMAIN = os.environ.get('CF_TEAM_DOMAIN')
CF_AUDIENCE = os.environ.get('CF_AUDIENCE')

def verify_cloudflare_jwt(token):
    """
    Verify Cloudflare Access JWT token
    """
    if not CF_TEAM_DOMAIN or not CF_AUDIENCE:
        # In development, allow access if env vars not set
        if os.environ.get('ENV') != 'production':
            return True
        return False
    
    try:
        # Get the public key from Cloudflare
        jwks_url = f"https://{CF_TEAM_DOMAIN}/cdn-cgi/access/certs"
        jwks = requests.get(jwks_url).json()
        
        # Decode and verify the JWT
        decoded = jwt.decode(
            token,
            jwks['public_certs'][0]['public_key'],
            audience=CF_AUDIENCE,
            algorithms=['RS256']
        )
        
        return True
    except Exception as e:
        print(f"Cloudflare JWT verification failed: {str(e)}")
        return False

def cloudflare_access_required(f):
    """
    Decorator to require Cloudflare Access authentication
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check for Cloudflare Access JWT in headers
        cf_jwt = request.headers.get('Cf-Access-Jwt-Assertion')
        
        if not cf_jwt:
            return jsonify({
                'error': 'Cloudflare Access authentication required',
                'message': 'Missing Cf-Access-Jwt-Assertion header'
            }), 401
        
        if not verify_cloudflare_jwt(cf_jwt):
            return jsonify({
                'error': 'Invalid Cloudflare Access token',
                'message': 'Token verification failed'
            }), 401
            
        return f(*args, **kwargs)
    return decorated_function