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
    Process videos with brand overlays: template, logo, and adaptive watermark
    """
    
    SAFE_ZONE_PERCENT = 0.05  # 5% padding from edges
    # Fixed opacity instead of adaptive brightness detection
    WATERMARK_OPACITY = 0.15
    
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
        brand_name = brand_config.get('name', 'Unknown')
        
        width = self.video_metadata['width']
        height = self.video_metadata['height']
        
        # Scale down videos wider than 720px for faster processing
        target_width = min(width, 720)
        scale_filter = f"scale={target_width}:-1" if width > 720 else "null"
        
        filters = []
        inputs = ['0:v']  # Start with video input
        
        print(f"[DEBUG] Building filter complex for brand: {brand_name}")
        print(f"[DEBUG] Video dimensions: {width}x{height}")
        
        # 1. Load and scale template
        template_path = os.path.join(PROJECT_ROOT, 'portal', 'imports', 'brands', assets.get('template', ''))
        print(f"[DEBUG] Template path: {template_path}")
        print(f"[DEBUG] Template exists: {os.path.exists(template_path)}")
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
            print(f"[DEBUG] Logo path: {logo_path}")
            print(f"[DEBUG] Logo exists: {os.path.exists(logo_path)}")
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
        watermark_path = os.path.join(PROJECT_ROOT, 'portal', 'imports', 'brands', assets.get('watermark', ''))
        print(f"[DEBUG] Watermark path: {watermark_path}")
        print(f"[DEBUG] Watermark exists: {os.path.exists(watermark_path)}")
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
                # No filters at all, create a fallback filter
                print("[DEBUG] No filters found, creating fallback filter")
                filters.append(f"[0:v]scale={width}:{height}[vout]")
                print(f"[DEBUG] Added fallback filter: {filters[-1]}")
        
        # Ensure exactly one [vout] label exists in the entire filter chain
        filter_complex = ';'.join(filters)
        
        # Validate that exactly one [vout] exists
        vout_count = filter_complex.count('[vout]')
        print(f"[DEBUG] Final filter complex: {filter_complex}")
        print(f"[DEBUG] Number of [vout] labels found: {vout_count}")
        
        # Add fallback if no [vout] label found
        if vout_count == 0:
            print("[WARNING] No [vout] label found, adding fallback filter")
            if filter_complex:
                filter_complex += f";[0:v]scale={width}:{height}[vout]"
            else:
                filter_complex = f"[0:v]scale={width}:{height}[vout]"
            print(f"[DEBUG] Updated filter complex with fallback: {filter_complex}")
        elif vout_count > 1:
            print(f"[ERROR] Multiple [vout] labels found ({vout_count}), filter complex may be malformed: {filter_complex}")
            # Return None to indicate error
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