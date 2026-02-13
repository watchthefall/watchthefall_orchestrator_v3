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
    # Logo sizing (15% of video width)
    LOGO_SCALE = 0.15
    # Logo padding from edges (pixels)
    LOGO_PADDING = 40
    
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
    
    def resolve_watermark_path(self, brand_name: str) -> Optional[str]:
        """
        Resolve watermark path from master assets based on brand and orientation.
        
        Naming patterns tried (in order):
        - {brand}_watermark.png
        - {Brand}_watermark.png (capitalized)
        - {brand.lower()}_watermark.png
        """
        orientation = self.detect_orientation()
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
    
    def resolve_logo_path(self, brand_name: str) -> Optional[str]:
        """
        Resolve logo path from master assets Circle folder.
        
        Strict pattern: {brand}_logo.png
        No fallback, no guessing.
        """
        # Strict naming: {BrandName}_logo.png
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
        watermark_path = self.resolve_watermark_path(brand_name)
        if watermark_path:
            print(f"[DEBUG] Adding full-frame watermark: {watermark_path}")
            # Scale watermark to exactly match video dimensions
            # Apply 40% opacity via geq filter
            opacity = self.WATERMARK_OPACITY
            filters.append(f"movie='{watermark_path}',scale={width}:{height},format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{opacity}*alpha(X,Y)'[watermark]")
            filters.append(f"[{current_input}][watermark]overlay=0:0[v1]")
            current_input = 'v1'
            print(f"[DEBUG] Watermark overlay added (full-frame, {int(opacity*100)}% opacity)")
        else:
            print(f"[WARNING] No watermark found for {brand_name}, skipping watermark overlay")
        
        # 2. LOGO bottom-right with padding
        logo_path = self.resolve_logo_path(brand_name)
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
        
        # Ensure final output is labeled [vout]
        if filters:
            # Replace last output label with [vout]
            last_filter = filters[-1]
            if '[v1]' in last_filter or '[v2]' in last_filter:
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