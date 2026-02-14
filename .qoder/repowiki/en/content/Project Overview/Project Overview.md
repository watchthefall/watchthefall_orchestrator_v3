# Project Overview

<cite>
**Referenced Files in This Document**
- [requirements.txt](file://requirements.txt)
- [app/orchestrator.py](file://app/orchestrator.py)
- [app/video_processor.py](file://app/video_processor.py)
- [app/brand_loader.py](file://app/brand_loader.py)
- [app/crop_module.py](file://app/crop_module.py)
- [app/logo_editor.py](file://app/logo_editor.py)
- [app/config.py](file://app/config.py)
- [imports/brands/wtf_orchestrator/brands.yml](file://imports/brands/wtf_orchestrator/brands.yml)
- [imports/brands/wtf_orchestrator/watermark.yml](file://imports/brands/wtf_orchestrator/watermark.yml)
- [imports/brands/wtf_orchestrator/manifest.yml](file://imports/brands/wtf_orchestrator/manifest.yml)
- [demo_orchestrator.py](file://demo_orchestrator.py)
- [downloader/batch_downloader.py](file://downloader/batch_downloader.py)
- [portal/app.py](file://portal/app.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)
10. [Appendices](#appendices)

## Introduction
WatchTheFall Orchestrator v3 is a multi-brand video processing pipeline designed to transform raw video content into brand-specific variants with adaptive watermarks and logos. It supports rapid batch processing across 25+ brand identities, enabling automated content creation optimized for social media platforms. The system emphasizes mobile-friendly workflows, safe zones for overlays, and intelligent watermark opacity tuned to video brightness.

Key capabilities:
- Multi-brand export from a single crop
- Adaptive watermark opacity based on video brightness
- Safe zone enforcement for logos and watermarks
- Batch downloading from supported platforms
- Web portal APIs for programmatic orchestration

## Project Structure
The repository is organized into modular components:
- app/: Core orchestration, video processing, brand loading, crop, and logo editing modules
- imports/brands/: Brand assets and configuration manifests
- portal/: Flask-based web portal with APIs for fetching, processing, and downloading videos
- downloader/: Asynchronous batch downloader for TikTok, Instagram, Twitter/X, and YouTube
- assets/, cookies/, debug_output/, exports/, imports/, output/, temp/: Supporting directories for assets, cookies, and outputs
- scripts/: Utility scripts for asset cleanup and standardization

```mermaid
graph TB
subgraph "Core Orchestrator"
ORCH["app/orchestrator.py"]
BRLOAD["app/brand_loader.py"]
CROP["app/crop_module.py"]
LOGO["app/logo_editor.py"]
PROC["app/video_processor.py"]
CFG["app/config.py"]
end
subgraph "Brand Assets"
BRDIR["imports/brands/wtf_orchestrator/brands.yml"]
WMCFG["imports/brands/wtf_orchestrator/watermark.yml"]
MANCFG["imports/brands/wtf_orchestrator/manifest.yml"]
end
subgraph "Portal"
PORTAL["portal/app.py"]
end
subgraph "Downloader"
BATCHDL["downloader/batch_downloader.py"]
end
ORCH --> CROP
ORCH --> LOGO
ORCH --> PROC
ORCH --> BRLOAD
BRLOAD --> BRDIR
PROC --> BRDIR
PROC --> WMCFG
PROC --> MANCFG
PORTAL --> PROC
PORTAL --> BRLOAD
BATCHDL --> PORTAL
```

**Diagram sources**
- [app/orchestrator.py](file://app/orchestrator.py#L1-L172)
- [app/brand_loader.py](file://app/brand_loader.py#L1-L499)
- [app/crop_module.py](file://app/crop_module.py#L1-L193)
- [app/logo_editor.py](file://app/logo_editor.py#L1-L132)
- [app/video_processor.py](file://app/video_processor.py#L1-L273)
- [app/config.py](file://app/config.py#L1-L18)
- [imports/brands/wtf_orchestrator/brands.yml](file://imports/brands/wtf_orchestrator/brands.yml#L1-L423)
- [imports/brands/wtf_orchestrator/watermark.yml](file://imports/brands/wtf_orchestrator/watermark.yml#L1-L3)
- [imports/brands/wtf_orchestrator/manifest.yml](file://imports/brands/wtf_orchestrator/manifest.yml#L1-L4)
- [portal/app.py](file://portal/app.py#L1-L800)
- [downloader/batch_downloader.py](file://downloader/batch_downloader.py#L1-L83)

**Section sources**
- [app/orchestrator.py](file://app/orchestrator.py#L1-L172)
- [app/brand_loader.py](file://app/brand_loader.py#L1-L499)
- [app/video_processor.py](file://app/video_processor.py#L1-L273)
- [app/crop_module.py](file://app/crop_module.py#L1-L193)
- [app/logo_editor.py](file://app/logo_editor.py#L1-L132)
- [app/config.py](file://app/config.py#L1-L18)
- [imports/brands/wtf_orchestrator/brands.yml](file://imports/brands/wtf_orchestrator/brands.yml#L1-L423)
- [imports/brands/wtf_orchestrator/watermark.yml](file://imports/brands/wtf_orchestrator/watermark.yml#L1-L3)
- [imports/brands/wtf_orchestrator/manifest.yml](file://imports/brands/wtf_orchestrator/manifest.yml#L1-L4)
- [portal/app.py](file://portal/app.py#L1-L800)
- [downloader/batch_downloader.py](file://downloader/batch_downloader.py#L1-L83)

## Core Components
- Orchestrator: Central coordinator that sequences crop, logo editor, and multi-brand export.
- Brand Loader: Discovers and loads brand configurations and assets from imports/brands.
- Crop Module: Provides aspect-ratio-aware cropping and metadata for downstream processing.
- Logo Editor: Positions logos with safe zones and enforces background-removed variants when available.
- Video Processor: Applies brand-specific overlays (template, logo, watermark) with adaptive opacity and safe zones.
- Portal: Flask API for fetching videos, processing with selected brands, and serving outputs.
- Downloader: Asynchronous batch downloader supporting TikTok, Instagram, Twitter/X, and YouTube.

Practical example (from demo):
- Use orchestrate() to process a downloaded video through crop → logo editor → multi-brand export.
- Select brands by name or display name; outputs are saved under exports/<brand>/<video_id>.mp4.

**Section sources**
- [app/orchestrator.py](file://app/orchestrator.py#L12-L172)
- [app/brand_loader.py](file://app/brand_loader.py#L168-L183)
- [app/crop_module.py](file://app/crop_module.py#L174-L193)
- [app/logo_editor.py](file://app/logo_editor.py#L117-L132)
- [app/video_processor.py](file://app/video_processor.py#L256-L273)
- [demo_orchestrator.py](file://demo_orchestrator.py#L13-L109)

## Architecture Overview
The system follows a staged pipeline orchestrated by WTFOrchestrator. It integrates external downloads (yt-dlp), interactive crop and logo editors, and a robust video processor that applies brand-specific assets with adaptive watermarking.

```mermaid
sequenceDiagram
participant User as "User"
participant Portal as "Flask Portal (portal/app.py)"
participant DL as "Downloader"
participant Orchestrator as "WTFOrchestrator"
participant Crop as "CropEditor"
participant Logo as "LogoEditor"
participant Processor as "VideoProcessor"
User->>Portal : POST /api/videos/fetch (optional)
Portal->>DL : download_batch(urls)
DL-->>Portal : results
User->>Portal : POST /api/videos/process_brands {url, brands}
Portal->>Portal : normalize_video()
Portal->>Processor : construct with normalized video
loop For each selected brand
Portal->>Processor : process_brand(brand_config, video_id)
Processor-->>Portal : output_path
end
Portal-->>User : download URLs for outputs
```

**Diagram sources**
- [portal/app.py](file://portal/app.py#L329-L627)
- [downloader/batch_downloader.py](file://downloader/batch_downloader.py#L53-L83)
- [app/video_processor.py](file://app/video_processor.py#L180-L253)

## Detailed Component Analysis

### Orchestrator
- Responsibilities: Coordinates crop, logo editor, and multi-brand export; aggregates results and errors.
- Pipeline stages: Crop → Brand load → Logo editor → Multi-brand export.
- Brand filtering: Accepts selected_brands or processes all brands.
- Output: Results dictionary with success status, stage metadata, outputs, and errors.

```mermaid
flowchart TD
Start(["Start"]) --> CropStage["Stage 1: Crop<br/>launch_crop_ui()"]
CropStage --> BrandsStage["Stage 2: Load Brands<br/>get_brands()"]
BrandsStage --> LogoStage["Stage 3: Logo Editor<br/>launch_logo_editor()"]
LogoStage --> ExportStage["Stage 4: Multi-brand Export<br/>process_video()"]
ExportStage --> End(["End"])
```

**Diagram sources**
- [app/orchestrator.py](file://app/orchestrator.py#L29-L115)

**Section sources**
- [app/orchestrator.py](file://app/orchestrator.py#L12-L172)

### Brand Loader
- Asset discovery: Scans imports/brands for brand directories and infers assets by filename patterns.
- Manifests: Loads manifest.yml, watermark.yml, orientation.yml, routing.yml, and platforms.yml.
- Top-level brands.yml: If present, merges with discovered assets and options.
- Integrity reporting: Generates an integrity report summarizing assets and warnings.

```mermaid
flowchart TD
A["imports/brands root"] --> B{"Top-level brands.yml exists?"}
B -- Yes --> C["Load brands.yml and merge with discovered assets"]
B -- No --> D["Scan directories and infer assets by patterns"]
C --> E["Return brand configs"]
D --> E
```

**Diagram sources**
- [app/brand_loader.py](file://app/brand_loader.py#L131-L183)

**Section sources**
- [app/brand_loader.py](file://app/brand_loader.py#L168-L183)
- [imports/brands/wtf_orchestrator/brands.yml](file://imports/brands/wtf_orchestrator/brands.yml#L1-L423)

### Crop Module
- Aspect ratios: Supports 9:16 (vertical), 1:1 (square), 4:5 (portrait), 16:9 (landscape).
- Crop calculation: Computes even-sized crop dimensions fitting within source video.
- Metadata: Returns crop settings and original/cropped dimensions for downstream use.

```mermaid
flowchart TD
S(["Source Video"]) --> Probe["Probe video with ffprobe"]
Probe --> AR{"Aspect Ratio"}
AR --> Calc["Compute crop width/height"]
Calc --> Even["Round to nearest even numbers"]
Even --> Apply["Apply crop with ffmpeg"]
Apply --> Meta["Build crop metadata"]
Meta --> O(["Cropped Video + Metadata"])
```

**Diagram sources**
- [app/crop_module.py](file://app/crop_module.py#L33-L171)

**Section sources**
- [app/crop_module.py](file://app/crop_module.py#L17-L193)

### Logo Editor
- Defaults: 15% of video width; top-left with safe zones.
- Safe zones: 5% padding from edges enforced during positioning.
- Background removal: Prefers logos_clean variants when available.

```mermaid
flowchart TD
L0(["Cropped Video + Crop Metadata"]) --> Defaults["Compute default logo size/position"]
Defaults --> Safe["Enforce safe zones (5%)"]
Safe --> Output(["Logo Settings + Logo Path"])
```

**Diagram sources**
- [app/logo_editor.py](file://app/logo_editor.py#L57-L114)

**Section sources**
- [app/logo_editor.py](file://app/logo_editor.py#L11-L132)

### Video Processor
- Adaptive watermark opacity: Brightness-based inverse mapping between 10% and 20%.
- Overlay order: Template → Logo → Watermark with safe zone and scaling.
- Options: watermark_position and watermark_scale from brand manifests.
- Output: One video per brand under exports/<brand>/<video_id>.mp4.

```mermaid
flowchart TD
V0(["Input Video"]) --> Bright["Calculate video brightness"]
Bright --> Opacity["Compute adaptive opacity"]
Opacity --> Filters["Build filter_complex:<br/>Template → Logo → Watermark"]
Filters --> Encode["Encode with ffmpeg (libx264)"]
Encode --> Out(["Output Paths"])
```

**Diagram sources**
- [app/video_processor.py](file://app/video_processor.py#L52-L178)

**Section sources**
- [app/video_processor.py](file://app/video_processor.py#L13-L273)
- [imports/brands/wtf_orchestrator/watermark.yml](file://imports/brands/wtf_orchestrator/watermark.yml#L1-L3)
- [imports/brands/wtf_orchestrator/manifest.yml](file://imports/brands/wtf_orchestrator/manifest.yml#L1-L4)

### Portal API
- Endpoints:
  - POST /api/videos/process_brands: Process video with selected brands; supports URL or local source_path.
  - POST /api/videos/fetch: Fetch multiple videos concurrently (up to 5).
  - GET /api/videos/download/<filename>: Serve processed outputs.
  - GET /api/brands/list: List available brands.
  - POST /api/videos/convert-watermark: Convert video with watermark (async job).
  - GET /api/videos/convert-status/<job_id>: Poll job status.
- Features:
  - Per-brand configuration overrides applied at runtime.
  - Health checks, storage diagnostics, and FFmpeg debug endpoints.
  - Cookie-based authentication for Instagram downloads.

```mermaid
sequenceDiagram
participant Client as "Client"
participant API as "Flask API (portal/app.py)"
participant YDL as "yt-dlp"
participant DB as "Database"
participant VP as "VideoProcessor"
Client->>API : POST /api/videos/fetch {urls}
API->>YDL : Download sequentially with retries
YDL-->>API : Local paths
API-->>Client : {successful, results}
Client->>API : POST /api/videos/process_brands {url/source_path, brands}
API->>DB : get_brand_config(brand) for each brand
API->>VP : process_brand(brand_config, video_id)
VP-->>API : output_path
API-->>Client : {outputs : [{brand, filename, download_url}]}
```

**Diagram sources**
- [portal/app.py](file://portal/app.py#L329-L627)

**Section sources**
- [portal/app.py](file://portal/app.py#L225-L290)
- [portal/app.py](file://portal/app.py#L329-L627)

### Downloader
- Async batch downloader: Detects platform and dispatches to platform-specific downloaders.
- Concurrency: Uses asyncio.gather to process multiple URLs concurrently.
- Robustness: Handles exceptions and returns structured results per URL.

```mermaid
flowchart TD
U["URLs"] --> Detect["Detect Platform"]
Detect --> Route{"Platform"}
Route --> |TikTok| T["download_tiktok_video"]
Route --> |Instagram| I["download_instagram_video"]
Route --> |Twitter/X| X["download_twitter_video"]
Route --> |YouTube| Y["download_youtube_video"]
T --> Gather["asyncio.gather"]
I --> Gather
X --> Gather
Y --> Gather
Gather --> R["Results"]
```

**Diagram sources**
- [downloader/batch_downloader.py](file://downloader/batch_downloader.py#L16-L83)

**Section sources**
- [downloader/batch_downloader.py](file://downloader/batch_downloader.py#L1-L83)

## Dependency Analysis
External dependencies include ffmpeg/ffprobe, Pillow, numpy, yt-dlp, and Flask ecosystem. The project resolves binary paths via environment variables and ensures directories exist at runtime.

```mermaid
graph TB
REQ["requirements.txt"] --> FFMPEG["ffmpeg/ffprobe"]
REQ --> PIL["Pillow"]
REQ --> NUMPY["numpy"]
REQ --> YTDL["yt-dlp"]
REQ --> FLASK["Flask/Werkzeug/Jinja2"]
REQ --> WEB["websockets/brotli"]
REQ --> PSUTIL["psutil"]
REQ --> JWT["pyjwt/cryptography"]
APP["app modules"] --> FFMPEG
APP --> PIL
APP --> NUMPY
PORTAL["portal/app.py"] --> FLASK
PORTAL --> YTDL
PORTAL --> FFMPEG
```

**Diagram sources**
- [requirements.txt](file://requirements.txt#L1-L18)
- [app/config.py](file://app/config.py#L11-L18)

**Section sources**
- [requirements.txt](file://requirements.txt#L1-L18)
- [app/config.py](file://app/config.py#L1-L18)

## Performance Considerations
- Adaptive watermark opacity reduces visual intrusion on bright videos, balancing visibility and aesthetics.
- Safe zones prevent overlays from being cut off on various devices.
- Sequential brand processing in the portal avoids memory pressure on constrained environments.
- FFmpeg encoding uses libx264 with moderate CRF and preset for balanced quality/speed.
- Batch downloading limits concurrent fetches to respect resource constraints.

## Troubleshooting Guide
Common issues and resolutions:
- No brands found: Verify imports/brands contains valid brand directories and assets; check brands.yml presence and correctness.
- Audio-only video: The system detects audio-only content and returns a specific error; retry with a video-enabled source.
- Missing FFmpeg: Use debug endpoints to confirm binary paths and availability.
- Storage permissions: Use storage debug endpoints to verify write permissions for upload/output/temp/log directories.
- Instagram access: Ensure cookies.txt is present and contains valid cookie data; otherwise, downloads may fail.

**Section sources**
- [portal/app.py](file://portal/app.py#L188-L214)
- [portal/app.py](file://portal/app.py#L92-L116)
- [portal/app.py](file://portal/app.py#L118-L162)
- [portal/app.py](file://portal/app.py#L569-L583)

## Conclusion
WatchTheFall Orchestrator v3 provides a scalable, multi-brand video processing pipeline with adaptive overlays and safe zone enforcement. Its modular design enables both command-line demos and a production-grade Flask portal for batch downloading and automated export. With 25+ brand identities supported out-of-the-box and extensible manifests, teams can rapidly produce platform-optimized content for social media distribution.

## Appendices
- Practical example usage is demonstrated in the demo script, showing orchestration steps and expected outputs.
- Brand coverage includes regional identities (e.g., ScotlandWTF, EnglandWTF, IrelandWTF) and thematic brands (e.g., AIWTF, GadgetsWTF).

**Section sources**
- [demo_orchestrator.py](file://demo_orchestrator.py#L13-L109)
- [imports/brands/wtf_orchestrator/brands.yml](file://imports/brands/wtf_orchestrator/brands.yml#L1-L423)