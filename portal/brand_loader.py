"""
Brand Loader - Load brand configurations from database
"""

import os
import json
from typing import Dict, List, Optional

def load_brand_configs(config_path: str) -> List[Dict]:
    """
    Legacy function - Load brand configurations from brand_config.json
    Used as fallback if database is empty
    
    Args:
        config_path: Path to brand_config.json
        
    Returns:
        List of brand configurations
    """
    try:
        with open(config_path, 'r') as f:
            data = json.load(f) or {}
        
        brands = []
        for brand_name, config in data.items():
            # Extract options correctly
            options = config.get('options', {})
            # If options is None or missing, use defaults
            if not options:
                options = {
                    'watermark_position': 'bottom-right',
                    'watermark_scale': 0.25
                }
            
            brand_config = {
                'name': brand_name,
                'display_name': config.get('display_name', brand_name),
                'assets': config.get('assets', {}),
                'options': options
            }
            brands.append(brand_config)
        
        return brands
    except Exception as e:
        print(f"Error loading brand configs: {e}")
        return []


def get_available_brands(portal_dir: str, user_id: int = None) -> List[Dict]:
    """
    Get available brands from database.
    Falls back to JSON config if database is empty.
    
    Args:
        portal_dir: Path to portal directory
        user_id: Optional user ID to filter brands (None = system brands only)
        
    Returns:
        List of brand configurations
    """
    try:
        from .database import get_all_brands
        
        # Get brands from database
        db_brands = get_all_brands(user_id=user_id, include_system=True)
        
        if db_brands:
            # Convert DB format to expected format
            brands = []
            for b in db_brands:
                brand_config = {
                    'id': b['id'],
                    'name': b['name'],
                    'display_name': b['display_name'],
                    'user_id': b['user_id'],
                    'is_system': bool(b['is_system']),
                    'is_locked': bool(b['is_locked']),
                    'watermark_vertical': b['watermark_vertical'],
                    'watermark_square': b['watermark_square'],
                    'watermark_landscape': b['watermark_landscape'],
                    'logo_path': b['logo_path'],
                    'options': {
                        'watermark_scale': b['watermark_scale'],
                        'watermark_opacity': b['watermark_opacity'],
                        'logo_scale': b['logo_scale'],
                        'logo_padding': b['logo_padding'],
                        'text_enabled': bool(b['text_enabled']),
                        'text_content': b['text_content'],
                        'text_position': b['text_position'],
                        'text_size': b['text_size'],
                        'text_color': b['text_color'],
                        'text_font': b['text_font'],
                        'text_bg_enabled': bool(b['text_bg_enabled']),
                        'text_bg_color': b['text_bg_color'],
                        'text_bg_opacity': b['text_bg_opacity'],
                        'text_margin': b['text_margin'],
                    }
                }
                brands.append(brand_config)
            
            print(f"[BRAND LOADER] Loaded {len(brands)} brands from database")
            return brands
        else:
            print("[BRAND LOADER] No brands in database, falling back to JSON config")
            config_path = os.path.join(portal_dir, 'brand_config.json')
            return load_brand_configs(config_path)
            
    except Exception as e:
        print(f"[BRAND LOADER] Database error: {e}, falling back to JSON config")
        config_path = os.path.join(portal_dir, 'brand_config.json')
        return load_brand_configs(config_path)


def get_brand_by_name(brand_name: str, user_id: int = None) -> Optional[Dict]:
    """
    Get a specific brand by name from database.
    
    Args:
        brand_name: Name of the brand
        user_id: Optional user ID for ownership check
        
    Returns:
        Brand configuration dict or None
    """
    try:
        from .database import get_brand
        
        brand = get_brand(name=brand_name, user_id=user_id)
        
        if brand:
            return {
                'id': brand['id'],
                'name': brand['name'],
                'display_name': brand['display_name'],
                'user_id': brand['user_id'],
                'is_system': bool(brand['is_system']),
                'is_locked': bool(brand['is_locked']),
                'watermark_vertical': brand['watermark_vertical'],
                'watermark_square': brand['watermark_square'],
                'watermark_landscape': brand['watermark_landscape'],
                'logo_path': brand['logo_path'],
                'options': {
                    'watermark_scale': brand['watermark_scale'],
                    'watermark_opacity': brand['watermark_opacity'],
                    'logo_scale': brand['logo_scale'],
                    'logo_padding': brand['logo_padding'],
                    'text_enabled': bool(brand['text_enabled']),
                    'text_content': brand['text_content'],
                    'text_position': brand['text_position'],
                    'text_size': brand['text_size'],
                    'text_color': brand['text_color'],
                    'text_font': brand['text_font'],
                    'text_bg_enabled': bool(brand['text_bg_enabled']),
                    'text_bg_color': brand['text_bg_color'],
                    'text_bg_opacity': brand['text_bg_opacity'],
                    'text_margin': brand['text_margin'],
                }
            }
        return None
    except Exception as e:
        print(f"[BRAND LOADER] Error getting brand {brand_name}: {e}")
        return None