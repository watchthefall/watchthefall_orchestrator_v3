"""
Video Processor - Apply template, logo, and watermark with adaptive opacity
Handles multi-brand export with safe zones and brightness-based watermark adjustment
"""
import os
import subprocess
import json
import time
from typing import Dict, List, Optional

# Import configuration
try:
    from config import FFMPEG_BIN, FFPROBE_BIN, PROJECT_ROOT
except ImportError:
    # Fallback when run standalone
    FFMPEG_BIN = 'ffmpeg'
    FFPROBE_BIN = 'ffprobe'
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def normalize_video(input_path: str) -> str:
    """
    Normalize video to standard 8-bit H264 SDR format, stripping HDR/DOVI metadata.
    
    This stage MUST run before branding to handle:
    - HEVC 10-bit Dolby Vision inputs
    - Corrupt timestamps from Instagram/TikTok
    - HDR colorspace metadata
    
    Args:
        input_path: Path to the input video file
        
    Returns:
        Path to normalized video file (or original if normalization fails)
    """
    try:
        fixed_path = input_path.replace(".mp4", "_normalized.mp4")
        print(f"[NORMALIZE] Normalizing video to clean 8-bit H264 SDR: {input_path}")
        
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", input_path,
            "-vf", "scale=720:-2",  # Scale to 720px width, maintain aspect ratio (even height)
            "-c:v", "libx264",  # Re-encode to H264 (NOT copy)
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",  # Force 8-bit SDR (strips HDR/10-bit)
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            fixed_path
        ]
        
        print(f"[NORMALIZE] Running command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0 and os.path.exists(fixed_path):
            file_size = os.path.getsize(fixed_path) / (1024 * 1024)
            print(f"[NORMALIZE] Successfully normalized video: {fixed_path} ({file_size:.2f}MB)")
            return fixed_path
        else:
            print(f"[NORMALIZE] Failed to normalize video. stderr: {result.stderr}")
            if os.path.exists(fixed_path):
                os.remove(fixed_path)  # Clean up failed output
            return input_path
    except Exception as e:
        print(f"[NORMALIZE] Error during normalization: {e}")
        return input_path


class VideoProcessor:
    """
    Process videos with brand overlays using dynamic master asset resolution.
    Assets resolved from WTF_MASTER_ASSETS/Branding/ based on video orientation.
    """
    
    # Master asset paths
    MASTER_ASSETS_ROOT = os.path.join(PROJECT_ROOT, 'WTF_MASTER_ASSETS', 'Branding')
    WATERMARKS_DIR = os.path.join(MASTER_ASSETS_ROOT, 'Watermarks')
    LOGOS_DIR = os.path.join(MASTER_ASSETS_ROOT, 'Logos', 'Circle')
    
    # Watermark full-frame opacity (40%)
    WATERMARK_OPACITY = 0.4
    # Watermark scale multiplier (1.0 = exact frame size, >1.0 = overscale to compensate for PNG padding)
    WATERMARK_SCALE = 1.15  # 15% overscale to fill frame when PNG has internal padding
    # Logo sizing (15% of video width)
    LOGO_SCALE = 0.15
    # Logo padding from edges (pixels)
    LOGO_PADDING = 40
    
    # Text layer settings (defaults)
    TEXT_ENABLED = False
    TEXT_CONTENT = ''
    TEXT_POSITION = 'bottom'  # top, bottom, center
    TEXT_SIZE = 48
    TEXT_COLOR = '#FFFFFF'
    TEXT_FONT = 'Arial'
    TEXT_BG_ENABLED = True
    TEXT_BG_COLOR = '#000000'
    TEXT_BG_OPACITY = 0.6
    TEXT_MARGIN = 40
    
    def __init__(self, video_path: str, output_dir: str = 'exports'):
        self.video_path = video_path
        self.output_dir = output_dir
        
        # Probe video info
        cmd = [FFPROBE_BIN, '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', video_path]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            self.video_info = json.loads(result.stdout)
            print(f"[DEBUG] Video info: {json.dumps(self.video_info, indent=2)}")
            
            # Extract key information
            format_info = self.video_info.get('format', {})
            streams = self.video_info.get('streams', [])
            
            print(f"[DEBUG] Format: {format_info.get('format_name', 'unknown')}")
            print(f"[DEBUG] Duration: {format_info.get('duration', 'unknown')} seconds")
            print(f"[DEBUG] Streams count: {len(streams)}")
            
            # Find video stream
            video_stream = None
            for stream in streams:
                if stream.get('codec_type') == 'video':
                    video_stream = stream
                    break
            
            if video_stream:
                self.video_metadata = {
                    'width': int(video_stream.get('width', 1080)),
                    'height': int(video_stream.get('height', 1920)),
                    'duration': float(format_info.get('duration', 0))
                }
            else:
                # Fallback if no video stream found
                self.video_metadata = {'width': 1080, 'height': 1920, 'duration': 0}
                
            print(f"[DEBUG] Video dimensions: {self.video_metadata['width']}x{self.video_metadata['height']}")
            
        except Exception as e:
            print(f"[ERROR] Failed to probe video: {e}")
            self.video_info = {}
            self.video_metadata = {'width': 1080, 'height': 1920, 'duration': 0}
    
    def has_video_stream(self) -> bool:
        """
        Check if the video file contains a valid video stream.
        
        Returns:
            bool: True if video stream exists, False otherwise
        """
        try:
            streams = self.video_info.get('streams', [])
            for stream in streams:
                if stream.get('codec_type') == 'video':
                    return True
            return False
        except Exception as e:
            print(f"[ERROR] Failed to check video stream: {e}")
            return False
    
    def detect_orientation(self) -> str:
        """
        Detect video orientation based on dimensions.
        
        Returns:
            'Vertical_HD' for portrait (height > width)
            'Square' for square (height == width)
            'Landscape' for landscape (width > height)
        """
        width = self.video_metadata['width']
        height = self.video_metadata['height']
        
        if height > width:
            orientation = 'Vertical_HD'
        elif width == height:
            orientation = 'Square'
        else:
            orientation = 'Landscape'
        
        print(f"[DEBUG] Detected orientation: {orientation} (w:{width} x h:{height})")
        return orientation
    
    def resolve_watermark_path(self, brand_name: str, brand_config: Dict = None) -> Optional[str]:
        """
        Resolve watermark path - from database uploaded watermark or master assets.
        
        Priority:
        1. DB watermark_path (uploaded SaaS watermark)
        2. DB-stored path based on orientation (legacy: watermark_vertical, watermark_square, watermark_landscape)
        3. Fallback to master assets filesystem resolution
        
        Args:
            brand_name: Name of the brand
            brand_config: Optional brand config dict with DB-stored paths
        """
        from .config import STORAGE_ROOT
        
        # 1. Try new uploaded watermark_path (SaaS model)
        if brand_config:
            watermark_path = brand_config.get('watermark_path')
            if watermark_path:
                # Path is relative to STORAGE_ROOT
                full_path = os.path.join(STORAGE_ROOT, watermark_path)
                if os.path.exists(full_path):
                    print(f"[DEBUG] Using uploaded watermark: {full_path}")
                    return full_path
                print(f"[DEBUG] Uploaded watermark path not found: {full_path}")
        
        # 2. Try legacy DB-stored path based on orientation
        orientation = self.detect_orientation()
        if brand_config:
            orientation_key = {
                'Vertical_HD': 'watermark_vertical',
                'Square': 'watermark_square',
                'Landscape': 'watermark_landscape'
            }.get(orientation)
            
            db_path = brand_config.get(orientation_key)
            if db_path:
                # DB path is relative to project root
                from .config import PROJECT_ROOT
                full_path = os.path.join(PROJECT_ROOT, db_path)
                if os.path.exists(full_path):
                    print(f"[DEBUG] Using DB watermark path: {full_path}")
                    return full_path
                print(f"[DEBUG] DB watermark path not found: {full_path}")
        
        # 3. Fallback to master assets filesystem resolution
        watermark_dir = os.path.join(self.WATERMARKS_DIR, orientation)
        
        # Clean brand name (remove 'WTF' suffix if present)
        clean_brand = brand_name.replace('WTF', '').strip()
        
        # Try different naming patterns
        patterns = [
            f"{clean_brand}_watermark.png",
            f"{clean_brand.lower()}_watermark.png",
            f"{clean_brand.capitalize()}_watermark.png",
            f"{brand_name}_watermark.png",
        ]
        
        for pattern in patterns:
            path = os.path.join(watermark_dir, pattern)
            print(f"[DEBUG] Trying watermark path: {path}")
            if os.path.exists(path):
                print(f"[DEBUG] Found watermark: {path}")
                return path
        
        print(f"[WARNING] No watermark found for {brand_name} in {watermark_dir}")
        return None
    
    def resolve_logo_path(self, brand_name: str, brand_config: Dict = None) -> Optional[str]:
        """
        Resolve logo path - from database uploaded logo or master assets.
        
        Priority:
        1. DB-stored logo_path (uploaded SaaS logo)
        2. Fallback to master assets Circle folder
        
        Args:
            brand_name: Name of the brand
            brand_config: Optional brand config dict with DB-stored paths
        """
        from .config import STORAGE_ROOT
        
        # 1. Try DB-stored logo_path (SaaS model)
        if brand_config:
            db_path = brand_config.get('logo_path')
            if db_path:
                # Path is relative to STORAGE_ROOT
                full_path = os.path.join(STORAGE_ROOT, db_path)
                if os.path.exists(full_path):
                    print(f"[DEBUG] Using uploaded logo: {full_path}")
                    return full_path
                print(f"[DEBUG] Uploaded logo path not found: {full_path}")
        
        # 2. Fallback to master assets Circle folder
        logo_filename = f"{brand_name}_logo.png"
        path = os.path.join(self.LOGOS_DIR, logo_filename)
        
        print(f"[DEBUG] Looking for logo: {path}")
        
        if os.path.exists(path):
            print(f"[DEBUG] Found logo: {path}")
            return path
        else:
            print(f"[ERROR] Logo not found for brand '{brand_name}'")
            print(f"[ERROR] Expected: {logo_filename}")
            print(f"[ERROR] All logos must follow pattern: {{BrandName}}_logo.png")
            return None
    
    def build_filter_complex(self, brand_config: Dict, logo_settings: Optional[Dict] = None) -> str:
        """
        Build ffmpeg filter_complex for overlays using dynamic master asset resolution.
        
        If brand_config contains new percent-based positioning fields (logo_x, wm_mode, etc),
        uses those. Otherwise falls back to legacy behavior.
        
        Pipeline:
        1. Watermark as FULL-FRAME or POSITIONED overlay
        2. Logo at saved position with saved opacity
        3. Optional text overlay at saved position
        """
        brand_name = brand_config.get('name', 'Unknown')
        
        # Check if brand has new visual positioning fields or secondary logo
        has_visual_fields = 'logo_x' in brand_config or 'wm_mode' in brand_config or brand_config.get('secondary_logo_enabled')
        
        if has_visual_fields:
            print(f"[DEBUG] Brand {brand_name} has visual positioning fields, using percent-based layout")
            return self.build_filter_complex_visual(brand_config, logo_settings)
        else:
            print(f"[DEBUG] Brand {brand_name} using legacy layout (no visual positioning)")
            return self.build_filter_complex_legacy(brand_config, logo_settings)
    
    def build_filter_complex_visual(self, brand_config: Dict, logo_settings: Optional[Dict] = None) -> str:
        """
        Build FFmpeg filter_complex from percent-based visual positioning fields.
        
        Uses saved percent coordinates (0-1) and converts to pixels based on output dimensions.
        """
        brand_name = brand_config.get('name', 'Unknown')
        
        # Output dimensions
        W = self.video_metadata['width']
        H = self.video_metadata['height']
        
        print(f"[VISUAL_PRESET] ========================================")
        print(f"[VISUAL_PRESET] Building filter for brand: {brand_name}")
        print(f"[VISUAL_PRESET] Output dimensions: {W}x{H}")
        
        filters = []
        current_input = '0:v'
        
        # Extract visual positioning fields with defaults
        logo_x_pct = brand_config.get('logo_x', 0.85)
        logo_y_pct = brand_config.get('logo_y', 0.85)
        logo_scale_pct = brand_config.get('logo_scale', 0.15)
        logo_opacity = brand_config.get('logo_opacity', 1.0)
        logo_rotation = brand_config.get('logo_rotation', 0.0)  # degrees (0-360)
        
        wm_mode = brand_config.get('wm_mode', 'fullscreen')
        wm_x_pct = brand_config.get('wm_x', 0.5)
        wm_y_pct = brand_config.get('wm_y', 0.5)
        wm_scale_pct = brand_config.get('wm_scale', 1.0)
        wm_opacity = brand_config.get('wm_opacity', 0.10)
        
        text_enabled = brand_config.get('text_enabled', False)
        text_content = brand_config.get('text_content', '')
        text_x_pct = brand_config.get('text_x_percent', 0.5)
        text_y_pct = brand_config.get('text_y_percent', 0.2)
        text_size = brand_config.get('text_size', 48)
        text_color = brand_config.get('text_color', '#FFFFFF')
        
        print(f"[VISUAL_PRESET] Logo: x={logo_x_pct:.2f}, y={logo_y_pct:.2f}, scale={logo_scale_pct:.2f}, opacity={logo_opacity:.2f}, rotation={logo_rotation}°")
        print(f"[VISUAL_PRESET] Watermark: mode={wm_mode}, x={wm_x_pct:.2f}, y={wm_y_pct:.2f}, scale={wm_scale_pct:.2f}, opacity={wm_opacity:.2f}")
        print(f"[VISUAL_PRESET] Text: enabled={text_enabled}, content='{text_content[:30]}', x={text_x_pct:.2f}, y={text_y_pct:.2f}")
        
        # 1. WATERMARK OVERLAY
        watermark_path = self.resolve_watermark_path(brand_name, brand_config)
        if watermark_path:
            print(f"[VISUAL_PRESET] Adding watermark: {watermark_path}")
            
            if wm_mode == 'fullscreen':
                # Fullscreen mode: scale to W:H, overlay at 0:0
                scaled_w = int(W * wm_scale_pct)
                scaled_h = int(H * wm_scale_pct)
                offset_x = (scaled_w - W) // 2
                offset_y = (scaled_h - H) // 2
                
                print(f"[VISUAL_PRESET] Watermark fullscreen: {scaled_w}x{scaled_h}, offset=(-{offset_x},-{offset_y}), opacity={wm_opacity:.2f}")
                
                filters.append(f"movie='{watermark_path}',scale={scaled_w}:{scaled_h},format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{wm_opacity}*alpha(X,Y)'[watermark]")
                filters.append(f"[{current_input}][watermark]overlay=-{offset_x}:-{offset_y}[v1]")
                current_input = 'v1'
            else:
                # Positioned mode: scale and overlay at specific position
                wm_target_w = int(wm_scale_pct * W * 0.5)  # Scale relative to width
                wm_x_px = int(wm_x_pct * W) - wm_target_w // 2
                wm_y_px = int(wm_y_pct * H) - wm_target_w // 2
                
                print(f"[VISUAL_PRESET] Watermark positioned: width={wm_target_w}px, pos=({wm_x_px},{wm_y_px}), opacity={wm_opacity:.2f}")
                
                filters.append(f"movie='{watermark_path}',scale={wm_target_w}:-1,format=rgba,colorchannelmixer=aa={wm_opacity}[watermark]")
                filters.append(f"[{current_input}][watermark]overlay={wm_x_px}:{wm_y_px}[v1]")
                current_input = 'v1'
        else:
            print(f"[VISUAL_PRESET] No watermark found, skipping")
        
        # 2. LOGO OVERLAY
        logo_path = self.resolve_logo_path(brand_name, brand_config)
        if logo_path:
            logo_target_w = int(logo_scale_pct * W)
            logo_x_px = int(logo_x_pct * W) - logo_target_w // 2
            logo_y_px = int(logo_y_pct * H) - logo_target_w // 2
            
            print(f"[VISUAL_PRESET] Adding logo: {logo_path}")
            print(f"[VISUAL_PRESET] Logo: width={logo_target_w}px, pos=({logo_x_px},{logo_y_px}), opacity={logo_opacity:.2f}, rotation={logo_rotation}°")
            
            # Build logo filter with optional rotation
            if logo_rotation != 0.0:
                # Convert degrees to radians for FFmpeg rotate filter
                rotation_rad = (logo_rotation * 3.14159265359) / 180.0
                print(f"[VISUAL_PRESET] Applying rotation: {logo_rotation}° = {rotation_rad:.4f} radians")
                
                # Apply scale -> rotate -> opacity in sequence
                filters.append(f"movie='{logo_path}',scale={logo_target_w}:-1,format=rgba,rotate={rotation_rad}:ow=hypot(iw,ih):oh=ow:fillcolor=0x00000000[logo_rotated]")
                filters.append(f"[logo_rotated]colorchannelmixer=aa={logo_opacity}[logo]")
            else:
                # No rotation - simple path
                filters.append(f"movie='{logo_path}',scale={logo_target_w}:-1,format=rgba,colorchannelmixer=aa={logo_opacity}[logo]")
            
            filters.append(f"[{current_input}][logo]overlay={logo_x_px}:{logo_y_px}[v2]")
            current_input = 'v2'
        else:
            print(f"[VISUAL_PRESET] No logo found, skipping")
        
        # 2b. SECONDARY LOGO OVERLAY (Dual-Logo Composition Mode, Platinum+)
        sec_logo_enabled = brand_config.get('secondary_logo_enabled', False)
        sec_logo_path = brand_config.get('secondary_logo_resolved_path')
        if sec_logo_enabled and sec_logo_path and os.path.exists(sec_logo_path):
            sec_scale = max(0.03, min(0.5, float(brand_config.get('secondary_logo_scale', 0.12))))
            sec_opacity = max(0.1, min(1.0, float(brand_config.get('secondary_logo_opacity', 0.9))))
            sec_x_pct = max(0.0, min(1.0, float(brand_config.get('secondary_logo_x', 0.15))))
            sec_y_pct = max(0.0, min(1.0, float(brand_config.get('secondary_logo_y', 0.15))))
            sec_rotation = float(brand_config.get('secondary_logo_rotation', 0)) % 360
            
            sec_target_w = int(sec_scale * W)
            sec_x_px = int(sec_x_pct * W) - sec_target_w // 2
            sec_y_px = int(sec_y_pct * H) - sec_target_w // 2
            
            print(f"[VISUAL_PRESET] Adding secondary logo: {sec_logo_path}")
            print(f"[VISUAL_PRESET] SecLogo: width={sec_target_w}px, pos=({sec_x_px},{sec_y_px}), opacity={sec_opacity:.2f}, rotation={sec_rotation}°")
            
            # Determine next overlay label
            if current_input.startswith('v') and current_input[1:].isdigit():
                next_v = f'v{int(current_input[1:]) + 1}'
            else:
                next_v = 'v1'
            
            if sec_rotation != 0:
                rotation_rad = (sec_rotation * 3.14159265359) / 180.0
                print(f"[VISUAL_PRESET] SecLogo rotation: {sec_rotation}° = {rotation_rad:.4f} radians")
                filters.append(f"movie='{sec_logo_path}',scale={sec_target_w}:-1,format=rgba,rotate={rotation_rad}:ow=hypot(iw,ih):oh=ow:fillcolor=0x00000000[sec_logo_r]")
                filters.append(f"[sec_logo_r]colorchannelmixer=aa={sec_opacity}[sec_logo]")
            else:
                filters.append(f"movie='{sec_logo_path}',scale={sec_target_w}:-1,format=rgba,colorchannelmixer=aa={sec_opacity}[sec_logo]")
            
            filters.append(f"[{current_input}][sec_logo]overlay={sec_x_px}:{sec_y_px}[{next_v}]")
            current_input = next_v
            print(f"[VISUAL_PRESET] Secondary logo overlay added -> [{next_v}]")
        elif sec_logo_enabled:
            print(f"[VISUAL_PRESET] Secondary logo enabled but file not found or missing, skipping")
        
        # 3. TEXT OVERLAY (if enabled)
        if text_enabled and text_content:
            text_x_px = int(text_x_pct * W)
            text_y_px = int(text_y_pct * H)
            
            print(f"[VISUAL_PRESET] Adding text: '{text_content[:30]}'")
            print(f"[VISUAL_PRESET] Text: size={text_size}px, pos=({text_x_px},{text_y_px}), color={text_color}")
            
            # Escape text for FFmpeg
            escaped_text = text_content.replace("'", "'\\''").replace(":", "\\:")
            text_color_hex = text_color.lstrip('#')
            
            # Build drawtext filter
            drawtext_filter = f"drawtext=text='{escaped_text}':fontsize={text_size}:fontcolor=0x{text_color_hex}:x={text_x_px}-text_w/2:y={text_y_px}-text_h/2:box=1:boxcolor=0x000000@0.6:boxborderw=10"
            
            next_label = 'v3' if current_input in ['v1', 'v2'] else ('v2' if current_input == '0:v' else f'v{int(current_input[1:]) + 1}' if current_input.startswith('v') and current_input[1:].isdigit() else 'v4')
            filters.append(f"[{current_input}]{drawtext_filter}[{next_label}]")
            current_input = next_label
        
        # Ensure final output is [vout]
        if filters:
            last_filter = filters[-1]
            # Replace the last [vN] label with [vout] generically
            if current_input.startswith('v') and current_input[1:].isdigit():
                filters[-1] = last_filter.rsplit('[', 1)[0] + '[vout]'
        else:
            # No overlays - passthrough
            filters.append(f"[0:v]scale={W}:{H}[vout]")
        
        filter_complex = ';'.join(filters)
        print(f"[VISUAL_PRESET] Final filter: {filter_complex}")
        print(f"[VISUAL_PRESET] ========================================")
        
        return filter_complex
    
    def build_filter_complex_legacy(self, brand_config: Dict, logo_settings: Optional[Dict] = None) -> str:
        """
        LEGACY: Build ffmpeg filter_complex for overlays using old hardcoded positioning.
        
        Pipeline:
        1. Watermark as FULL-FRAME overlay at 40% opacity (from master assets)
        2. Logo bottom-right at 15% width (from master assets Circle folder)
        
        No template overlay - watermark IS the full-frame overlay.
        """
        brand_name = brand_config.get('name', 'Unknown')
        
        width = self.video_metadata['width']
        height = self.video_metadata['height']
        
        filters = []
        current_input = '0:v'
        
        print(f"[DEBUG] Building filter complex for brand: {brand_name}")
        print(f"[DEBUG] Video dimensions: {width}x{height}")
        print(f"[DEBUG] Master assets root: {self.MASTER_ASSETS_ROOT}")
        
        # 1. WATERMARK as full-frame overlay (replaces old template concept)
        watermark_path = self.resolve_watermark_path(brand_name, brand_config)
        if watermark_path:
            print(f"[DEBUG] Adding full-frame watermark: {watermark_path}")
            # Scale watermark with multiplier to compensate for internal PNG padding
            # Scale multiplier controlled by WATERMARK_SCALE (default 1.15 = 15% overscale)
            # W:H only exists in overlay context, not inside movie= source chain
            scaled_width = int(width * self.WATERMARK_SCALE)
            scaled_height = int(height * self.WATERMARK_SCALE)
            # Center the overscaled watermark to maintain visual balance
            offset_x = (scaled_width - width) // 2
            offset_y = (scaled_height - height) // 2
            opacity = self.WATERMARK_OPACITY
            filters.append(f"movie='{watermark_path}',scale={scaled_width}:{scaled_height},format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{opacity}*alpha(X,Y)'[watermark]")
            filters.append(f"[{current_input}][watermark]overlay=-{offset_x}:-{offset_y}[v1]")
            current_input = 'v1'
            print(f"[DEBUG] Watermark overlay added (overscaled {scaled_width}x{scaled_height} @ {int(self.WATERMARK_SCALE*100)}%, {int(opacity*100)}% opacity)")
        else:
            print(f"[WARNING] No watermark found for {brand_name}, skipping watermark overlay")
        
        # 2. LOGO bottom-right with padding
        logo_path = self.resolve_logo_path(brand_name, brand_config)
        if logo_path:
            print(f"[DEBUG] Adding logo: {logo_path}")
            # Scale logo to 15% of video width
            logo_width = int(width * self.LOGO_SCALE)
            padding = self.LOGO_PADDING
            
            # Position: bottom-right with padding
            logo_x = f"W-w-{padding}"
            logo_y = f"H-h-{padding}"
            
            # Add colorkey filter to remove black background if present
            filters.append(f"movie='{logo_path}',scale={logo_width}:-1,format=rgba,colorkey=black:0.1:0.1[logo]")
            filters.append(f"[{current_input}][logo]overlay={logo_x}:{logo_y}[v2]")
            current_input = 'v2'
            print(f"[DEBUG] Logo overlay added (bottom-right, {self.LOGO_SCALE*100:.0f}% width, {padding}px padding)")
        else:
            print(f"[WARNING] No logo found for {brand_name}, skipping logo overlay")
        
        # 3. TEXT LAYER (drawtext filter)
        if self.TEXT_ENABLED and self.TEXT_CONTENT:
            print(f"[DEBUG] Adding text layer: '{self.TEXT_CONTENT}'")
            
            # Escape special characters for FFmpeg drawtext
            escaped_text = self.TEXT_CONTENT.replace("'", "'\\''").replace(":", "\\:")
            
            # Convert hex color to FFmpeg format (remove # prefix)
            text_color = self.TEXT_COLOR.lstrip('#')
            bg_color = self.TEXT_BG_COLOR.lstrip('#')
            
            # Calculate position based on TEXT_POSITION setting
            margin = self.TEXT_MARGIN
            if self.TEXT_POSITION == 'top':
                y_pos = margin
            elif self.TEXT_POSITION == 'center':
                y_pos = '(h-text_h)/2'
            else:  # bottom (default)
                y_pos = f'h-text_h-{margin}'
            
            # Build drawtext filter
            font_size = self.TEXT_SIZE
            
            # Add background box if enabled
            if self.TEXT_BG_ENABLED:
                # FFmpeg box opacity (0-1)
                box_opacity = self.TEXT_BG_OPACITY
                drawtext_filter = f"drawtext=text='{escaped_text}':fontsize={font_size}:fontcolor=0x{text_color}:x=(w-text_w)/2:y={y_pos}:box=1:boxcolor=0x{bg_color}@{box_opacity}:boxborderw=10"
            else:
                drawtext_filter = f"drawtext=text='{escaped_text}':fontsize={font_size}:fontcolor=0x{text_color}:x=(w-text_w)/2:y={y_pos}"
            
            # Determine next label
            next_label = 'v3' if current_input in ['v1', 'v2'] else 'v2'
            filters.append(f"[{current_input}]{drawtext_filter}[{next_label}]")
            current_input = next_label
            print(f"[DEBUG] Text layer added (position={self.TEXT_POSITION}, size={font_size}, bg={self.TEXT_BG_ENABLED})")
        else:
            print(f"[DEBUG] Text layer disabled or empty, skipping")
        
        # Ensure final output is labeled [vout]
        if filters:
            # Replace last output label with [vout]
            last_filter = filters[-1]
            if '[v1]' in last_filter or '[v2]' in last_filter or '[v3]' in last_filter:
                filters[-1] = last_filter.rsplit('[', 1)[0] + '[vout]'
            print(f"[DEBUG] Final output labeled as [vout]")
        else:
            # No overlays at all - just pass through with scale
            print("[WARNING] No overlays applied, creating passthrough filter")
            filters.append(f"[0:v]scale={width}:{height}[vout]")
        
        filter_complex = ';'.join(filters)
        
        # Validate [vout] exists
        vout_count = filter_complex.count('[vout]')
        print(f"[DEBUG] Final filter complex: {filter_complex}")
        print(f"[DEBUG] Number of [vout] labels: {vout_count}")
        
        if vout_count != 1:
            print(f"[ERROR] Invalid [vout] count ({vout_count}), filter may be malformed")
            return None
        
        return filter_complex
    
    def process_brand(self, brand_config: Dict, logo_settings: Optional[Dict] = None, 
                     video_id: str = 'video') -> str:
        """
        Process video with brand overlays
        
        Args:
            brand_config: Brand configuration from brands.yml
            logo_settings: Logo position and size settings (optional)
            video_id: Identifier for output filename
        
        Returns:
            Path to processed video
        """
        start_time = time.time()
        brand_name = brand_config.get('name', 'brand')
        output_filename = f"{video_id}_{brand_name}.mp4"
        output_path = os.path.join(self.output_dir, output_filename)
        
        print(f"[DEBUG] Processing brand: {brand_name}")
        print(f"[DEBUG] Video ID: {video_id}")
        print(f"[DEBUG] Output filename: {output_filename}")
        print(f"[DEBUG] Output path: {output_path}")
        
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        print(f"[DEBUG] Writing branded video to: {output_path}")
        
        # Build filter complex
        filter_complex = self.build_filter_complex(brand_config, logo_settings)
        
        print(f"[DEBUG] Built filter complex result: {filter_complex}")
        print(f"[FILTER_COMPLEX] ========================================")
        print(f"[FILTER_COMPLEX] EXACT STRING FOR {brand_name}:")
        print(f"[FILTER_COMPLEX] {filter_complex}")
        print(f"[FILTER_COMPLEX] =========================================")
        
        # If filter_complex generation failed, return error
        if filter_complex is None:
            error_msg = f"[ERROR] Failed to generate valid filter_complex for brand {brand_name}"
            print(error_msg)
            raise Exception(error_msg)
        
        # If no valid filter_complex, return error instead of copying
        if not filter_complex or '[vout]' not in filter_complex:
            error_msg = f"[ERROR] No valid filter complex with [vout] for brand {brand_name}"
            print(error_msg)
            raise Exception(error_msg)
        
        # Check if the input video has a valid video stream before processing
        if not self.has_video_stream():
            error_msg = "[ERROR] The input file contains no valid video stream (audio-only). Instagram may have served audio-only content."
            print(error_msg)
            raise Exception(error_msg)
        
        # Run ffmpeg with optimized settings for Render Pro environments
        cmd = [
            FFMPEG_BIN, '-y',
            '-i', self.video_path,
            '-filter_complex', filter_complex,
            '-threads', '4',
            '-filter_threads', '2',
            '-bufsize', '256M',
            '-map', '[vout]',
            '-map', '0:a?',
            '-c:v', 'libx264',
            '-crf', '23',
            '-preset', 'fast',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-movflags', '+faststart',
            output_path
        ]
        
        try:
            print(f"[DEBUG] Executing FFmpeg command with Render Pro optimizations: {' '.join(cmd)}")
            # Add verbose output to see what's happening
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            processing_time = time.time() - start_time
            print(f"  Processing {brand_name} completed in {processing_time:.2f} seconds")
            print(f"[DEBUG] FFmpeg stdout: {result.stdout}")
            print(f"[DEBUG] FFmpeg stderr: {result.stderr}")
            print(f"[DEBUG] File exists after FFmpeg: {os.path.exists(output_path)}")
            if os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                print(f"[DEBUG] Output file size: {file_size} bytes")
            return output_path
        except subprocess.CalledProcessError as e:
            error_msg = f"FFmpeg error: {e.stderr}\nFFmpeg stdout: {e.stdout}"
            print(error_msg)
            raise Exception(error_msg)
    
    def process_multiple_brands(self, brands: List[Dict], logo_settings: Optional[Dict] = None,
                               video_id: str = 'video') -> List[str]:
        """
        Process video for multiple brands
        
        Args:
            brands: List of brand configurations
            logo_settings: Logo settings (same for all brands if provided)
            video_id: Identifier for output filenames
        
        Returns:
            List of output video paths
        """
        output_paths = []
        
        for brand in brands:
            brand_name = brand.get('name', 'Unknown')
            print(f"  Processing {brand_name}...")
            
            try:
                output_path = self.process_brand(brand, logo_settings, video_id)
                output_paths.append(output_path)
                print(f"    ✓ Exported to {output_path}")
            except Exception as e:
                error_msg = f"    ✗ Failed: {e}"
                print(error_msg)
                # Don't raise exception, continue with other brands
                # But we could choose to raise if we want to stop on first error
        
        return output_paths


def process_video(video_path: str, brands: List[Dict], logo_settings: Optional[Dict] = None,
                 output_dir: str = 'exports', video_id: str = 'video') -> List[str]:
    """
    Convenience function to process video for multiple brands
    
    Args:
        video_path: Path to cropped video
        brands: List of brand configurations
        logo_settings: Logo position/size settings
        output_dir: Output directory
        video_id: Video identifier
    
    Returns:
        List of output video paths
    """
    processor = VideoProcessor(video_path, output_dir)
    return processor.process_multiple_brands(brands, logo_settings, video_id)


def load_brand_configs(config_path: str) -> List[Dict]:
    """
    Load brand configurations from brand_config.json
    
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
            brand_config = {
                'name': brand_name,
                'display_name': config.get('display_name', brand_name),
                'assets': config.get('assets', {}),
                'options': config.get('options', {
                    'watermark_position': 'bottom-right',
                    'watermark_scale': 0.25
                })
            }
            brands.append(brand_config)
        
        return brands
    except Exception as e:
        print(f"Error loading brand configs: {e}")
        return []


def get_available_brands(portal_dir: str) -> List[Dict]:
    """
    Get available brands from portal/wtf_brands directory
    
    Args:
        portal_dir: Path to portal directory
        
    Returns:
        List of brand configurations
    """
    config_path = os.path.join(portal_dir, 'brand_config.json')
    return load_brand_configs(config_path)