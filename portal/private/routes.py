"""
Private Portal Routes with Cloudflare Access Protection
"""
from flask import Blueprint, jsonify, request
from .cloudflare_auth import cloudflare_access_required

# Create blueprint for private portal
private_portal = Blueprint('private_portal', __name__, url_prefix='/portal')

@private_portal.route('/private/test')
@cloudflare_access_required
def private_test():
    """Test endpoint for private portal"""
    return jsonify({
        'status': 'success',
        'message': 'Private portal access granted via Cloudflare Access',
        'protected': True
    })

@private_portal.route('/private/status')
@cloudflare_access_required
def private_status():
    """Status endpoint for private portal"""
    return jsonify({
        'status': 'online',
        'service': 'WatchTheFall Orchestrator v3',
        'protection': 'Cloudflare Access',
        'access': 'granted'
    })