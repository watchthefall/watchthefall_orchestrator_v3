"""
Image Processing Utilities for Logo/Watermark Normalization
Handles format conversion, background removal, and asset optimization
"""
from PIL import Image, ImageOps
import os
import numpy as np


def normalize_logo(input_path, output_path, max_dimension=1024, remove_bg=None, bg_strength=50):
    """
    Normalize uploaded logo for consistent rendering
    
    Args:
        input_path: Path to uploaded file
        output_path: Path to save normalized PNG
        max_dimension: Maximum width/height (default 1024px)
        remove_bg: 'dark', 'light', or None
        bg_strength: Aggressiveness 0-150 (higher = more removal)
    
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
            img = remove_background(img, mode=remove_bg, strength=bg_strength)
        
        # Auto-trim transparent/dead space around visible content
        bbox = img.getbbox()
        if bbox is not None:
            # Add safe padding margin (10px)
            padding = 10
            left = max(0, bbox[0] - padding)
            top = max(0, bbox[1] - padding)
            right = min(img.width, bbox[2] + padding)
            bottom = min(img.height, bbox[3] + padding)
            img = img.crop((left, top, right, bottom))
        # If bbox is None, image is fully transparent; save as-is
        
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


def remove_background(img, mode='light', strength=30):
    """
    Aggressive color-distance background removal.
    
    Key improvements:
    - No early validation bailouts - always attempts removal
    - Non-linear tolerance curve for aggressive cleanup at high values
    - Works on off-white, beige, gray, and colored backgrounds
    
    Args:
        img: PIL Image (any mode)
        mode: 'light' (remove bright), 'dark' (remove dark), or 'none'
        strength: 0-150+ (higher = more aggressive removal)
    
    Returns:
        PIL Image with background made transparent
    """
    if mode == 'none' or not img:
        return img
    
    # Convert to RGBA for alpha channel manipulation
    img = img.convert("RGBA")
    data = np.array(img)
    
    # Sample all 4 corners to estimate background color
    # Using mean instead of median for speed (corner pixels usually uniform)
    h, w = data.shape[0], data.shape[1]
    corner_pixels = [
        data[0, 0],                    # Top-left
        data[0, w-1],                  # Top-right  
        data[h-1, 0],                  # Bottom-left
        data[h-1, w-1]                 # Bottom-right
    ]
    
    # Average the corner colors (ignore alpha for RGB calculation)
    bg_color = np.mean([p[:3] for p in corner_pixels], axis=0)
    bg_brightness = np.mean(bg_color)
    
    # Calculate effective tolerance based on strength
    # Non-linear curve: 0-100 is linear, 100+ adds exponential boost
    if strength <= 100:
        # Linear mapping: strength 50 → tolerance ~90
        effective_tolerance = (strength / 100.0) * 180.0
    else:
        # Aggressive mode: strength 150 → tolerance ~270
        base_tolerance = 180.0  # At strength=100
        extra_boost = (strength - 100) * 1.8
        effective_tolerance = base_tolerance + extra_boost
    
    # Clamp to reasonable range (never go below 10 or above 380)
    effective_tolerance = max(10, min(effective_tolerance, 380))
    
    # Extract RGB channels (ignore existing alpha)
    rgb = data[:, :, :3]
    
    # Calculate Euclidean distance from background color
    # This measures "how similar" each pixel is to the corners
    distance = np.linalg.norm(rgb - bg_color, axis=2)
    
    # Create mask: pixels close enough to background color get removed
    # For light mode: also check that pixel is actually bright
    # For dark mode: also check that pixel is actually dark
    if mode == 'light':
        # Remove bright pixels similar to corners
        brightness = np.mean(rgb, axis=2)
        mask = (distance < effective_tolerance) & (brightness > 128)
    elif mode == 'dark':
        # Remove dark pixels similar to corners
        brightness = np.mean(rgb, axis=2)
        mask = (distance < effective_tolerance) & (brightness < 128)
    else:
        # Unknown mode - just use distance
        mask = distance < effective_tolerance
    
    # Apply transparency to matched pixels
    data[mask, 3] = 0
    
    return Image.fromarray(data)


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
