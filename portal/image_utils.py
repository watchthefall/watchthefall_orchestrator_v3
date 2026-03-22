"""
Image Processing Utilities for Logo/Watermark Normalization
Handles format conversion, background removal, and asset optimization
"""
from PIL import Image, ImageOps
import os
import numpy as np


def normalize_logo(input_path, output_path, max_dimension=1024, remove_bg=None, bg_threshold=30):
    """
    Normalize uploaded logo for consistent rendering
    
    Args:
        input_path: Path to uploaded file
        output_path: Path to save normalized PNG
        max_dimension: Maximum width/height (default 1024px)
        remove_bg: 'dark', 'light', or None
        bg_threshold: Sensitivity 0-255 (lower = more aggressive)
    
    Returns:
        dict with success status and metadata
    """
    try:
        # Open image
        img = Image.open(input_path)
        
        # Convert to RGBA (handles JPG, PNG, WebP, etc.)
        if img.mode != 'RGBA':
            # If RGB, add full opacity alpha channel
            img = img.convert('RGBA')
        
        # Auto-orient based on EXIF
        img = ImageOps.exif_transpose(img)
        
        # Resize if too large (maintain aspect ratio)
        if max(img.size) > max_dimension:
            img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        
        # Apply background removal if requested
        if remove_bg in ['dark', 'light']:
            img = remove_background(img, mode=remove_bg, threshold=bg_threshold)
        
        # Save as clean PNG
        img.save(output_path, 'PNG', optimize=True)
        
        return {
            'success': True,
            'original_format': Image.open(input_path).format,
            'original_size': Image.open(input_path).size,
            'normalized_size': img.size,
            'has_transparency': img.mode == 'RGBA'
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def remove_background(img, mode='dark', threshold=30):
    """
    Remove solid backgrounds using color threshold
    
    Args:
        img: PIL Image in RGBA mode
        mode: 'dark' (remove black/dark) or 'light' (remove white/bright)
        threshold: 0-255, lower = more aggressive removal
    
    Returns:
        PIL Image with background removed
    """
    # Convert to numpy array for pixel manipulation
    data = np.array(img)
    
    # Extract RGB channels
    r, g, b, a = data[:, :, 0], data[:, :, 1], data[:, :, 2], data[:, :, 3]
    
    if mode == 'dark':
        # Remove dark pixels (black backgrounds)
        # Calculate brightness (average of RGB)
        brightness = (r.astype(int) + g.astype(int) + b.astype(int)) / 3
        # Set alpha to 0 where brightness is below threshold
        mask = brightness < threshold
        a[mask] = 0
        
    elif mode == 'light':
        # Remove light pixels (white backgrounds)
        brightness = (r.astype(int) + g.astype(int) + b.astype(int)) / 3
        # Set alpha to 0 where brightness is above threshold
        mask = brightness > (255 - threshold)
        a[mask] = 0
    
    # Update alpha channel
    data[:, :, 3] = a
    
    # Convert back to PIL Image
    return Image.fromarray(data, 'RGBA')


def detect_solid_background(img_path):
    """
    Detect if image has a solid background (no transparency)
    
    Returns:
        dict with detection results
    """
    try:
        img = Image.open(img_path)
        
        # Check if image has alpha channel
        if img.mode != 'RGBA':
            return {
                'has_solid_bg': True,
                'reason': 'No transparency channel (JPG or RGB)',
                'avg_alpha': 255
            }
        
        # Convert to numpy and check alpha channel
        data = np.array(img)
        alpha = data[:, :, 3]
        
        # Calculate average alpha
        avg_alpha = np.mean(alpha)
        
        # If average alpha is close to 255, it's fully opaque
        has_solid_bg = avg_alpha > 250
        
        return {
            'has_solid_bg': has_solid_bg,
            'avg_alpha': float(avg_alpha),
            'reason': 'Fully opaque' if has_solid_bg else 'Has transparency'
        }
        
    except Exception as e:
        return {
            'has_solid_bg': False,
            'error': str(e)
        }
