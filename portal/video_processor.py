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

class VideoProcessor:
    """
    Process videos with brand overlays: template, logo, and adaptive watermark
    """
    
    SAFE_ZONE_PERCENT = 0.05  # 5% padding from edges
    # Fixed opacity instead of adaptive brightness detection
    WATERMARK_OPACITY = 0.15
    
    def __init__(self, video_path: str, output_dir: str = 'exports'):
        self.video_path = video_path
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        self.video_info = self._probe_video()
        # Removed brightness calculation to speed up processing
    
    def _probe_video(self) -> Dict:
        """Get video properties"""
        try:
            cmd = [
                FFPROBE_BIN, '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height,duration',
                '-of', 'json',
                self.video_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            stream = data['streams'][0]
            
            return {
                'width': int(stream['width']),
                'height': int(stream['height']),
                'duration': float(stream.get('duration', 0))
            }
        except:
            return {'width': 1080, 'height': 1920, 'duration': 0}
    
    # Removed calculate_video_brightness function - no longer needed
    
    # Removed calculate_adaptive_watermark_opacity function - using fixed opacity
    
    def build_filter_complex(self, brand_config: Dict, logo_settings: Optional[Dict] = None) -> str:
        """
        Build ffmpeg filter_complex for overlays
        
        Order:
        1. Scale template to video size
        2. Overlay template
        3. Overlay logo (if settings provided)
        4. Overlay watermark with fixed opacity
        """
        assets = brand_config.get('assets', {})
        options = brand_config.get('options', {})
        
        width = self.video_info['width']
        height = self.video_info['height']
        
        # Scale down videos wider than 720px for faster processing
        target_width = min(width, 720)
        scale_filter = f"scale={target_width}:-1" if width > 720 else "null"
        
        filters = []
        inputs = ['0:v']  # Start with video input
        
        print(f"[DEBUG] Building filter complex for brand: {brand_config.get('name', 'Unknown')}")
        print(f"[DEBUG] Video dimensions: {width}x{height}")
        
        # 1. Load and scale template
        template_path = os.path.join(PROJECT_ROOT, 'portal', 'wtf_brands', assets.get('template', ''))
        if os.path.exists(template_path):
            print(f"[DEBUG] Adding template: {template_path}")
            # Scale template to match scaled video
            template_scale = f"scale={target_width}:-1" if width > 720 else f"scale={width}:{height}"
            filters.append(f"movie='{template_path}',{template_scale}[template]")
            filters.append(f"[{inputs[-1]}][template]overlay=0:0[v1]")
            inputs.append('v1')
            print(f"[DEBUG] Template overlay added, current inputs: {inputs}")
        else:
            print(f"[DEBUG] No template found at: {template_path}")
        
        # 2. Overlay logo (if settings provided)
        if logo_settings and logo_settings.get('logo_path'):
            logo_path = logo_settings['logo_path']
            if os.path.exists(logo_path):
                print(f"[DEBUG] Adding logo: {logo_path}")
                logo_x = logo_settings['logo_settings']['x']
                logo_y = logo_settings['logo_settings']['y']
                logo_w = logo_settings['logo_settings']['width']
                logo_h = logo_settings['logo_settings']['height']
                
                # Scale logo appropriately
                logo_scale_w = int(logo_w * (target_width / width)) if width > 720 else logo_w
                logo_scale_h = int(logo_h * (target_width / width)) if width > 720 else logo_h
                logo_x_scaled = int(logo_x * (target_width / width)) if width > 720 else logo_x
                logo_y_scaled = int(logo_y * (target_width / width)) if width > 720 else logo_y
                
                filters.append(f"movie='{logo_path}',scale={logo_scale_w}:{logo_scale_h}[logo]")
                filters.append(f"[{inputs[-1]}][logo]overlay={logo_x_scaled}:{logo_y_scaled}[v2]")
                inputs.append('v2')
                print(f"[DEBUG] Logo overlay added, current inputs: {inputs}")
            else:
                print(f"[DEBUG] Logo file not found: {logo_path}")
        else:
            print("[DEBUG] No logo settings provided or logo path missing")
        
        # 3. Overlay watermark with fixed opacity using faster geq filter
        watermark_path = os.path.join(PROJECT_ROOT, 'portal', 'wtf_brands', assets.get('watermark', ''))
        if os.path.exists(watermark_path):
            print(f"[DEBUG] Adding watermark: {watermark_path}")
            wm_scale = options.get('watermark_scale', 0.25)
            # Scale watermark appropriately
            wm_width = int((width * wm_scale) * (target_width / width)) if width > 720 else int(width * wm_scale)
            
            # Calculate position (bottom-right with safe zone)
            safe_margin = int(width * self.SAFE_ZONE_PERCENT)
            wm_position = options.get('watermark_position', 'bottom-right')
            
            # Scale position coordinates
            if width > 720:
                scale_factor = target_width / width
                safe_margin = int(safe_margin * scale_factor)
            
            if wm_position == 'bottom-right':
                wm_x = f"W-w-{safe_margin}"
                wm_y = f"H-h-{safe_margin}"
            elif wm_position == 'bottom-left':
                wm_x = safe_margin
                wm_y = f"H-h-{safe_margin}"
            elif wm_position == 'top-right':
                wm_x = f"W-w-{safe_margin}"
                wm_y = safe_margin
            elif wm_position == 'top-left':
                wm_x = safe_margin
                wm_y = safe_margin
            else:
                wm_x = f"W-w-{safe_margin}"
                wm_y = f"H-h-{safe_margin}"
            
            # Use faster geq filter instead of colorchannelmixer for opacity
            filters.append(f"movie='{watermark_path}',scale={wm_width}:-1,format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='0.15*alpha(X,Y)'[watermark]")
            filters.append(f"[{inputs[-1]}][watermark]overlay={wm_x}:{wm_y}[vout]")
            print(f"[DEBUG] Watermark overlay added with [vout] label")
        else:
            print(f"[DEBUG] No watermark found at: {watermark_path}")
            # If no watermark, ensure the final output is labeled as [vout]
            if len(filters) > 0:
                # Get the last filter and ensure it ends with [vout]
                last_filter = filters[-1]
                print(f"[DEBUG] Last filter before modification: {last_filter}")
                if not last_filter.endswith('[vout]'):
                    # Remove any existing output label and add [vout]
                    # Find the last bracket and remove everything after it
                    if '[' in last_filter and last_filter.rfind('[') > last_filter.rfind('='):
                        # Remove existing output label
                        last_bracket = last_filter.rfind('[')
                        base_filter = last_filter[:last_bracket]
                        filters[-1] = f"{base_filter}[vout]"
                        print(f"[DEBUG] Modified last filter to add [vout]: {filters[-1]}")
                    else:
                        # No existing output label, just append [vout]
                        filters[-1] = f"{last_filter}[vout]"
                        print(f"[DEBUG] Appended [vout] to last filter: {filters[-1]}")
            else:
                # No filters at all, return None
                print("[DEBUG] No filters to process, returning None")
                return None
        
        # Ensure exactly one [vout] label exists in the entire filter chain
        filter_complex = ';'.join(filters)
        
        # Validate that exactly one [vout] exists
        vout_count = filter_complex.count('[vout]')
        print(f"[DEBUG] Final filter complex: {filter_complex}")
        print(f"[DEBUG] Number of [vout] labels found: {vout_count}")
        
        if vout_count == 0:
            print("[ERROR] No [vout] label found in filter complex")
            return None
        elif vout_count > 1:
            print(f"[WARNING] Multiple [vout] labels found ({vout_count}), filter complex may be malformed: {filter_complex}")
        
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
        output_path = os.path.join(self.output_dir, brand_name, output_filename)
        
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        print(f"[DEBUG] Writing branded video to: {output_path}")
        
        # Build filter complex
        filter_complex = self.build_filter_complex(brand_config, logo_settings)
        
        print(f"[DEBUG] Built filter complex result: {filter_complex}")
        
        if not filter_complex or '[vout]' not in filter_complex:
            # No overlays or no vout label - just copy
            print(f"[DEBUG] No valid filter complex with [vout], copying original video: {self.video_path} to {output_path}")
            import shutil
            shutil.copy2(self.video_path, output_path)
            processing_time = time.time() - start_time
            print(f"  Processing {brand_name} completed in {processing_time:.2f} seconds (copy only)")
            return output_path
        
        # Run ffmpeg with optimized settings for Render Pro environments
        cmd = [
            FFMPEG_BIN, '-y',
            '-threads', '2',  # Increase threads to 2 for Render Pro
            '-use_wallclock_as_timestamps', '1',  # Use wallclock timestamps
            '-fflags', '+genpts',  # Generate presentation timestamps
            '-i', self.video_path,
            '-filter_complex', filter_complex,
            '-filter_threads', '2',  # Increase filter threads to 2
            '-bufsize', '64M',  # Increase buffer size for better performance
            '-map', '[vout]',  # Explicitly map video output from filter_complex
            '-map', '0:a?',  # Map audio stream if present
            '-c:v', 'libx264',
            '-crf', '23',  # Maintain quality with CRF 23
            '-preset', 'faster',  # Use faster preset for better quality/speed balance
            '-c:a', 'aac',  # Re-encode audio instead of copying
            '-b:a', '128k',  # Set audio bitrate
            '-movflags', '+faststart',  # Fast start for web playback
            output_path
        ]
        
        try:
            print(f"[DEBUG] Executing FFmpeg command with Render Pro optimizations: {' '.join(cmd)}")
            result = subprocess.run(cmd, check=True, capture_output=True)
            processing_time = time.time() - start_time
            print(f"  Processing {brand_name} completed in {processing_time:.2f} seconds")
            return output_path
        except subprocess.CalledProcessError as e:
            print(f"FFmpeg error: {e.stderr.decode()}")
            print(f"FFmpeg stdout: {e.stdout.decode()}")
            raise
    
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
                print(f"    ✗ Failed: {e}")
        
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