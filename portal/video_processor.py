"""
Video Processor - Apply template, logo, and watermark with adaptive opacity
Handles multi-brand export with safe zones and brightness-based watermark adjustment
"""
import os
import shutil
import subprocess
import threading
import uuid as _uuid
import json
import time
import uuid
from typing import Dict, List, Optional

# ── FFmpeg runs BELOW the web worker's priority ───────────────────────────────
# The box is 1 CPU. FFmpeg already self-limits with -threads 1, but one thread is
# the whole machine, so the gunicorn worker and FFmpeg compete for the same core
# and FFmpeg wins by default. Observed 31 Aug: Render's /health probe arrives
# every 5s for 13 minutes, then goes SILENT for a full 60s the instant a render
# starts (~12 probes missed), resuming only once the encode is under way. Access
# logs are written on completion, so the 200s that follow are late answers to
# probes Render had already timed out at 5s. That is the same starvation that
# killed the instance on 26 Aug ("health check failed (timed out after 5 seconds)").
#
# `nice` fixes the cause rather than the symptom: the kernel gives the CPU to the
# web worker whenever it wants it and leaves the rest to FFmpeg. Renders get
# marginally slower; the service stays answerable throughout.
#
# Prefixing the command is deliberate over preexec_fn=os.nice: renders run inside
# threads, and preexec_fn forks from a multithreaded process, which Python's own
# docs flag as unsafe. Resolved once at import so a missing binary degrades to
# today's behaviour instead of failing every render.
FFMPEG_NICE = os.environ.get('FFMPEG_NICE', '10')
_NICE_BIN = shutil.which('nice')
NICE_PREFIX = [_NICE_BIN, '-n', FFMPEG_NICE] if _NICE_BIN else []
if not _NICE_BIN:
    print('[NICE] `nice` not found — FFmpeg will run at normal priority', flush=True)

# ── Normalize cache: one encode per distinct media transformation ─────────────
# Every submit site sends a single brand_id, so a 5-brand x 2-format batch is 10
# separate jobs — and each one used to normalize the same source independently.
# Measured 31 Aug: normalize took ~110s against renders of 105s/102s, i.e. about
# half the total work, repeated identically per job.
#
# The identity is the FFmpeg command itself rather than a hand-listed set of
# parameters. A parameter list is the design where forgetting a field means one
# brand silently inherits another's framing; an identical command provably
# produces identical output, and it cannot drift when a new transform flag is
# added later.
#
# Excluded from the identity, because they do not change the media:
#   - the executable path (may differ between environments)
#   - the `nice` wrapper (scheduling priority, not a transformation)
#   - the output path (what we are computing)
# Everything else is retained: input path, filters, crop/zoom/flip, dimensions,
# frame rate, codecs, pixel format, audio handling, and any flag added in future.
NORMALIZE_CACHE_VERSION = 'v1'   # bump to invalidate every cached file at once

_OUTPUT_PLACEHOLDER = '<<NORMALIZE_OUTPUT>>'

_norm_locks = {}
_norm_locks_guard = threading.Lock()


def _normalize_identity(cmd):
    """Stable key for the media transformation this command performs.

    cmd[0] is the executable and cmd[-1] is the output placeholder; both are
    dropped. The `nice` prefix is added after this point, so it is never part of
    the hash — changing FFMPEG_NICE must not throw away the cache.
    """
    import hashlib
    payload = '\x1f'.join(str(a) for a in cmd[1:-1])
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]
    return f'{NORMALIZE_CACHE_VERSION}-{digest}'


def _lock_for(key):
    """One lock per identity, so a second job waits rather than duplicating work."""
    with _norm_locks_guard:
        lk = _norm_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            # Bound the dict: unique (source, format, edit) combinations accumulate
            # over a long-lived process. Locks nobody holds are safe to forget.
            if len(_norm_locks) > 512:
                for k in [k for k, v in _norm_locks.items() if not v.locked()]:
                    _norm_locks.pop(k, None)
            _norm_locks[key] = lk
        return lk

# Import configuration
try:
    from config import FFMPEG_BIN, FFPROBE_BIN, PROJECT_ROOT
except ImportError:
    # Fallback when run standalone
    FFMPEG_BIN = 'ffmpeg'
    FFPROBE_BIN = 'ffprobe'
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NormalizationError(RuntimeError):
    """Normalization could not produce the required file.

    Raised instead of silently returning the original input. The old behaviour —
    fall back to the un-reframed source and carry on — converted a technical
    failure into a SUCCESSFUL-LOOKING but semantically wrong product result:
    on 31 Aug a vertical_9_16 request produced a 1280x720 landscape file, which
    then reached the charge-on-success path and cost the user a credit.
    A failed render is recoverable; a wrong one that claims success is not.
    """


# Dimensions each live output format is contractually required to produce.
# Used to verify the FINISHED file rather than trusting the request.
EXPECTED_OUTPUT_DIMS = {
    'vertical_9_16': (720, 1280),
    'square_1_1':    (720, 720),
    # 1280x720 rather than the 720-wide convention: 720x405 (404 after
    # even-rounding) is too small to be a credible YouTube deliverable. Same
    # target pixel count as vertical, which removes the obvious output-side
    # cost objection -- it is NOT a claim that render time is equal, since that
    # also depends on source resolution, scaling direction and filter cost.
    'landscape_16_9': (1280, 720),
}


def probe_dimensions(path):
    """Measured (width, height) of a finished file, or (None, None).

    Deliberately separate from _validate_output's boolean contract, and
    deliberately non-fatal on its own: a probe that cannot read the file is a
    METADATA failure, not evidence the media is wrong. The caller decides.
    """
    try:
        cmd = [FFPROBE_BIN, '-v', 'quiet', '-print_format', 'json',
               '-show_streams', path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"[PROBE] ffprobe failed (code={result.returncode}) — {path}", flush=True)
            return None, None
        for s in json.loads(result.stdout or '{}').get('streams', []):
            if s.get('codec_type') == 'video':
                w, h = s.get('width'), s.get('height')
                if w and h:
                    return int(w), int(h)
        print(f"[PROBE] no video stream with dimensions — {path}", flush=True)
        return None, None
    except Exception as e:
        print(f"[PROBE] dimension probe failed: {e} — {path}", flush=True)
        return None, None


MEDIA_PROBE_TIMEOUT = 30


def probe_media(path, timeout: int = MEDIA_PROBE_TIMEOUT):
    """Admissibility gate for a file ENTERING the pipeline.

    Establishes four things: ffprobe can read the container, a real video stream
    exists, it has usable dimensions, and the duration is positive.

    Deliberately does NOT decode frames. Frame decoding is expensive and the
    normalize/render stages already own it -- and they now fail loudly rather
    than silently substituting the source (NormalizationError, 31 Aug). The job
    here is only to stop a file that can NEVER render from becoming a Library
    entry the user can select, so the failure lands at upload with a reason
    instead of minutes later with "render failed".

    Deliberately separate from probe_dimensions(), whose contract is the
    opposite: a probe failure there is a METADATA problem about an output that
    already passed validation, so it is non-fatal. Here a probe failure IS the
    verdict about an unknown input.

    Returns (ok: bool, reason: str, meta: dict). `reason` is user-facing.
    """
    meta = {}
    if not path or not os.path.exists(path):
        return False, 'the file could not be found after upload', meta
    try:
        if os.path.getsize(path) == 0:
            return False, 'the file is empty', meta
    except OSError:
        return False, 'the file could not be read', meta

    cmd = [FFPROBE_BIN, '-v', 'error', '-print_format', 'json',
           '-show_format', '-show_streams', path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Bounded on purpose: this box runs a single worker, so an unbounded
        # probe on a pathological file would pin it.
        print(f"[MEDIA-PROBE] timed out after {timeout}s — {path}", flush=True)
        return False, f'the file could not be inspected within {timeout} seconds', meta
    except Exception as e:
        print(f"[MEDIA-PROBE] probe error: {e} — {path}", flush=True)
        return False, 'the file could not be inspected', meta

    if result.returncode != 0:
        tail = (result.stderr or '').strip().splitlines()[-1:] or ['']
        print(f"[MEDIA-PROBE] reject: ffprobe code={result.returncode} — {tail[0]}", flush=True)
        return False, 'this is not a readable video file', meta

    try:
        info = json.loads(result.stdout or '{}')
    except ValueError:
        return False, 'this is not a readable video file', meta

    streams = info.get('streams') or []
    # An MP3 with cover art carries a video stream whose disposition is
    # attached_pic -- a still image, not footage. Excluded, or an audio file
    # renamed to .mp4 would sail through this gate.
    video = next((s for s in streams
                  if s.get('codec_type') == 'video'
                  and not (s.get('disposition') or {}).get('attached_pic')), None)
    if video is None:
        print(f"[MEDIA-PROBE] reject: no video stream — {path}", flush=True)
        return False, 'this file has no video track', meta

    width, height = video.get('width'), video.get('height')
    try:
        width, height = int(width), int(height)
    except (TypeError, ValueError):
        width = height = 0
    if width <= 0 or height <= 0:
        print(f"[MEDIA-PROBE] reject: bad dimensions {width}x{height} — {path}", flush=True)
        return False, 'this video has no usable dimensions', meta

    def _as_seconds(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    # Either source is acceptable: some containers carry duration only on the
    # format, some only on the stream.
    duration = max(_as_seconds((info.get('format') or {}).get('duration')),
                   _as_seconds(video.get('duration')))
    if duration <= 0:
        print(f"[MEDIA-PROBE] reject: duration={duration} — {path}", flush=True)
        return False, 'this video has no usable duration', meta

    meta = {
        'width': width,
        'height': height,
        'duration': round(duration, 3),
        'codec': video.get('codec_name'),
        'has_audio': any(s.get('codec_type') == 'audio' for s in streams),
    }
    print(f"[MEDIA-PROBE] accept: {width}x{height} {duration:.1f}s "
          f"codec={meta['codec']} audio={meta['has_audio']} — {path}", flush=True)
    return True, '', meta


# ============================ INTRO / OUTRO COMPOSITION ======================
#
# THE CONTRACT, established by experiment on 4 Sep 2026 rather than assumed.
#
# Stream-copy concat IS reliable -- 227 frames = 150 + 77 exactly, full decode
# with zero stderr, +0.024s duration drift -- but ONLY when every segment shares
# one encoding contract. The dangerous part is what happens otherwise: concat
# does not refuse mismatched inputs, it silently produces a plausible file.
#
#   48kHz branded + 44.1kHz outro, not conformed
#       -> concat exit 0, decode clean, dimensions right, total duration right
#       -> audio stream 6.983s against a 7.567s video: the outro plays ~8.8%
#          fast and the last 0.58s is silent. RMS across the junction looks
#          perfectly continuous.
#
#   branded with no audio + outro with audio
#       -> concat exit 0, decode clean
#       -> the outro's audio track is silently DROPPED
#
# normalize_video never pins -ar and real sources are a mix of 44100 and 48000,
# so the first case is production-real. Hence: conform everything, and verify
# the ARTIFACT rather than the exit code.
CONFORM_CACHE_VERSION = 'v1'
CONCAT_AUDIO_RATE = 44100
CONCAT_AUDIO_CHANNELS = 2
CONCAT_AUDIO_BITRATE = '128k'
CONCAT_FPS = 30
COMPOSE_DURATION_TOLERANCE = 0.35      # composed total vs sum of segments
COMPOSE_AV_SKEW_TOLERANCE = 0.50       # audio vs video, BOTH directions
COMPOSE_ENCODE_TIMEOUT = 300
COMPOSE_DECODE_TIMEOUT = 300


class CompositionError(RuntimeError):
    """Intro/outro composition failed or produced an artifact off contract.

    Fatal by design, like NormalizationError: a composed output that is wrong is
    worse than one that never appeared, because the wrong one reaches the user
    and costs a credit.
    """


def _stream_summary(path):
    """(video_stream, audio_stream, format_dict) from one ffprobe call."""
    try:
        r = subprocess.run(
            [FFPROBE_BIN, '-v', 'error', '-print_format', 'json',
             '-show_format', '-show_streams', path],
            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return None, None, {}
        info = json.loads(r.stdout or '{}')
    except Exception as e:
        print(f"[COMPOSE] probe failed: {e} - {path}", flush=True)
        return None, None, {}
    streams = info.get('streams') or []
    video = next((x for x in streams
                  if x.get('codec_type') == 'video'
                  and not (x.get('disposition') or {}).get('attached_pic')), None)
    audio = next((x for x in streams if x.get('codec_type') == 'audio'), None)
    return video, audio, (info.get('format') or {})


def _as_seconds(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def media_duration(path):
    """Best available duration in seconds, or 0.0."""
    video, _audio, fmt = _stream_summary(path)
    return max(_as_seconds(fmt.get('duration')),
               _as_seconds((video or {}).get('duration')))


def _conformed_path(asset_path, output_format, key):
    base, _ext = os.path.splitext(asset_path)
    safe = ''.join(c for c in str(key) if c.isalnum() or c in ('-', '_'))[:40]
    return f"{base}_conformed_{output_format}_{safe}.mp4"


def conform_bookend(asset_path, output_format, target_w, target_h):
    """Bring an intro/outro asset onto the concat contract. Cached per (asset, format).

    The shipped assets are HEVC at 1048x1920 or 1060x1920 -- widths that are not
    even consistent with each other -- against renders that are H.264 at
    720x1280 / 720x720 / 1280x720. Conforming is therefore mandatory, not an
    optimisation, and geometry reuses _build_reframe_filter (Fit + blur-pad) so a
    bookend is fitted exactly the way every other aspect mismatch in Brandr is.

    Audio is GUARANTEED on the output: an asset without a track gets synthesised
    silence, because a segment missing audio makes concat drop the other
    segment's audio entirely rather than fail.
    """
    if not asset_path or not os.path.exists(asset_path):
        raise CompositionError(f'bookend asset missing: {asset_path}')

    ok, reason, _meta = probe_media(asset_path)
    if not ok:
        raise CompositionError(f'bookend asset unusable ({reason}): {asset_path}')

    _v, audio, _f = _stream_summary(asset_path)
    fit = {'crop_x': 0.5, 'crop_y': 0.5, 'zoom': 1.0, 'crop_mode': 'fit', 'flip_h': 0}
    reframe = _build_reframe_filter(asset_path, fit, target_w, target_h,
                                    f'bookend_{output_format}')
    if not reframe:
        raise CompositionError(f'could not build conform filter for {asset_path}')

    if audio is not None:
        cmd = [FFMPEG_BIN, '-y', '-threads', '1', '-i', asset_path,
               '-filter_complex', reframe, '-filter_threads', '1',
               '-map', '[out]', '-map', '0:a']
    else:
        cmd = [FFMPEG_BIN, '-y', '-threads', '1', '-i', asset_path,
               '-f', 'lavfi', '-i', f'anullsrc=r={CONCAT_AUDIO_RATE}:cl=stereo',
               '-filter_complex', reframe, '-filter_threads', '1',
               '-map', '[out]', '-map', '1:a', '-shortest']
    cmd += ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
            '-pix_fmt', 'yuv420p', '-r', str(CONCAT_FPS), '-threads', '1',
            '-c:a', 'aac', '-b:a', CONCAT_AUDIO_BITRATE,
            '-ar', str(CONCAT_AUDIO_RATE), '-ac', str(CONCAT_AUDIO_CHANNELS),
            '-movflags', '+faststart']

    # _normalize_identity returns 'v<n>-<hash>'; take just the hash so the key
    # reads v1-<hash> rather than v1-v1-<hash>. The conform cache carries its OWN
    # version so it can be invalidated without disturbing normalized files.
    _digest = _normalize_identity(cmd + ['<out>']).rsplit('-', 1)[-1]
    key = f'{CONFORM_CACHE_VERSION}-{_digest}'
    dest = _conformed_path(asset_path, output_format, key)

    if os.path.exists(dest):
        print(f"[CONFORM] HIT {key} - {os.path.basename(dest)}", flush=True)
        return dest

    with _lock_for(key):
        if os.path.exists(dest):          # someone else finished while we waited
            print(f"[CONFORM] HIT (after wait) {key}", flush=True)
            return dest
        tmp = f'{os.path.splitext(dest)[0]}.{_uuid.uuid4().hex}.tmp.mp4'
        run_cmd = NICE_PREFIX + cmd + [tmp]
        print(f"[CONFORM] MISS {key} - conforming {os.path.basename(asset_path)} "
              f"to {output_format} {target_w}x{target_h}", flush=True)
        try:
            r = subprocess.run(run_cmd, capture_output=True, text=True,
                               timeout=COMPOSE_ENCODE_TIMEOUT)
        except subprocess.TimeoutExpired:
            _discard_work_file(tmp)
            raise CompositionError(f'conform timed out for {asset_path}')
        if r.returncode != 0 or not os.path.exists(tmp):
            _discard_work_file(tmp)
            raise CompositionError(
                f'conform failed (code={r.returncode}) for {asset_path}: '
                f'{(r.stderr or "")[-300:]}')
        os.replace(tmp, dest)             # atomic publish, same as everywhere else
        print(f"[CONFORM] published {os.path.basename(dest)}", flush=True)
    return dest


def conform_branded_for_concat(branded_path, work_stem):
    """Give the branded segment concat-compatible audio. NEVER re-encodes video.

    Returns (path, created) -- `created` says whether a new file was made, so the
    caller knows what to clean up. When the audio already matches the contract
    the original path is returned untouched and nothing is spent.
    """
    _video, audio, _fmt = _stream_summary(branded_path)
    matches = (audio is not None
               and int(_as_seconds(audio.get('sample_rate'))) == CONCAT_AUDIO_RATE
               and int(audio.get('channels') or 0) == CONCAT_AUDIO_CHANNELS)
    if matches:
        print(f"[COMPOSE] branded audio already on contract "
              f"({CONCAT_AUDIO_RATE}Hz x{CONCAT_AUDIO_CHANNELS}) - copying as-is",
              flush=True)
        return branded_path, False

    out = f'{work_stem}.segment.{_uuid.uuid4().hex}.tmp.mp4'
    if audio is None:
        # No audio at all -- the drop-audio rung of the render ladder fired.
        # Synthesise silence so concat cannot drop the bookend's audio instead.
        print("[COMPOSE] branded segment has NO audio - synthesising silence", flush=True)
        cmd = [FFMPEG_BIN, '-y', '-i', branded_path,
               '-f', 'lavfi', '-i', f'anullsrc=r={CONCAT_AUDIO_RATE}:cl=stereo',
               '-map', '0:v', '-map', '1:a', '-shortest']
    else:
        print(f"[COMPOSE] branded audio is {audio.get('sample_rate')}Hz "
              f"x{audio.get('channels')} - re-encoding AUDIO ONLY", flush=True)
        cmd = [FFMPEG_BIN, '-y', '-i', branded_path]
    cmd += ['-c:v', 'copy',
            '-c:a', 'aac', '-b:a', CONCAT_AUDIO_BITRATE,
            '-ar', str(CONCAT_AUDIO_RATE), '-ac', str(CONCAT_AUDIO_CHANNELS),
            '-movflags', '+faststart', out]
    try:
        r = subprocess.run(NICE_PREFIX + cmd, capture_output=True, text=True,
                           timeout=COMPOSE_ENCODE_TIMEOUT)
    except subprocess.TimeoutExpired:
        _discard_work_file(out)
        raise CompositionError('branded-segment audio conform timed out')
    if r.returncode != 0 or not os.path.exists(out):
        _discard_work_file(out)
        raise CompositionError(
            f'branded-segment audio conform failed (code={r.returncode}): '
            f'{(r.stderr or "")[-300:]}')
    return out, True


def concat_copy(segments, out_path):
    """concat demuxer + -c copy. The branded video's pixels are never re-encoded."""
    listing = f'{os.path.splitext(out_path)[0]}.{_uuid.uuid4().hex}.concat.txt'
    quote = chr(39)
    try:
        with open(listing, 'w', encoding='utf-8') as fh:
            for seg in segments:
                # The concat demuxer treats ' as a quote character. Our own names
                # never contain one, but escaping now costs nothing and removes a
                # class of failure if asset naming ever changes.
                safe = os.path.abspath(seg).replace(chr(92), '/')
                safe = safe.replace(quote, quote + chr(92) + quote + quote)
                fh.write('file ' + quote + safe + quote + chr(10))
        cmd = NICE_PREFIX + [FFMPEG_BIN, '-y', '-f', 'concat', '-safe', '0',
                             '-i', listing, '-c', 'copy',
                             '-movflags', '+faststart', out_path]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=COMPOSE_ENCODE_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise CompositionError('concat timed out')
        if r.returncode != 0 or not os.path.exists(out_path):
            raise CompositionError(
                f'concat failed (code={r.returncode}): {(r.stderr or "")[-300:]}')
    finally:
        _discard_work_file(listing)
    return out_path


def verify_composition(path, expected_duration, target_w, target_h):
    """The artifact contract. Raises CompositionError on any breach.

    Every check here exists because something that passes the OTHER checks can
    still be wrong. The 48kHz experiment produced a file with a valid container,
    a clean full decode, exact dimensions and the correct TOTAL duration whose
    audio was nonetheless 0.58s short of its video. Only comparing the two
    stream durations against each other catches that.
    """
    video, audio, fmt = _stream_summary(path)
    if video is None:
        raise CompositionError('composed output has no video stream')

    w, h = video.get('width'), video.get('height')
    if (w, h) != (target_w, target_h):
        raise CompositionError(
            f'composed output is {w}x{h} but the format requires {target_w}x{target_h}')

    v_dur = max(_as_seconds(fmt.get('duration')), _as_seconds(video.get('duration')))
    if v_dur <= 0:
        raise CompositionError('composed output has no usable duration')

    if abs(v_dur - expected_duration) > COMPOSE_DURATION_TOLERANCE:
        raise CompositionError(
            f'composed duration {v_dur:.3f}s differs from the expected '
            f'{expected_duration:.3f}s by more than {COMPOSE_DURATION_TOLERANCE}s '
            f'- a segment is probably missing')

    if audio is None:
        raise CompositionError('composed output lost its audio track')
    a_dur = _as_seconds(audio.get('duration')) or _as_seconds(fmt.get('duration'))
    # BOUNDED BOTH WAYS. A short audio stream means a sample-rate mismatch played
    # a segment at the wrong speed; a long one is the same fault in reverse.
    # Checking only the floor would accept the mirror image of the bug.
    if abs(a_dur - v_dur) > COMPOSE_AV_SKEW_TOLERANCE:
        raise CompositionError(
            f'audio runs {a_dur:.3f}s against {v_dur:.3f}s of video '
            f'(skew {a_dur - v_dur:+.3f}s) - segments are not on one audio contract')

    # Decode every frame. ffprobe reads headers; only a decode reads the picture
    # data, and this project has already shipped one file whose header was
    # perfect and whose bitstream was shredded.
    try:
        r = subprocess.run([FFMPEG_BIN, '-v', 'error', '-i', path, '-f', 'null', '-'],
                           capture_output=True, text=True, timeout=COMPOSE_DECODE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise CompositionError('composed output could not be decoded within '
                               f'{COMPOSE_DECODE_TIMEOUT}s')
    if r.returncode != 0 or (r.stderr or '').strip():
        raise CompositionError(
            f'composed output does not decode cleanly: {(r.stderr or "")[-300:]}')

    print(f"[COMPOSE] verified {w}x{h} video={v_dur:.3f}s audio={a_dur:.3f}s "
          f"(expected {expected_duration:.3f}s) - full decode clean", flush=True)
    return {'width': w, 'height': h, 'duration': v_dur, 'audio_duration': a_dur}


def compose_bookends(branded_path, out_path, output_format, target_w, target_h,
                     intro_path=None, outro_path=None):
    """branded [+ intro] [+ outro] -> one validated MP4 at out_path.

    Order is deterministic: intro, branded, outro. Nothing is published here --
    the caller owns the atomic publish, so a composition that fails leaves the
    previously delivered artifact untouched.
    """
    if not intro_path and not outro_path:
        raise CompositionError('compose_bookends called with no bookends')

    work_stem = os.path.splitext(out_path)[0]
    temporaries = []
    try:
        segment, created = conform_branded_for_concat(branded_path, work_stem)
        if created:
            temporaries.append(segment)

        parts, expected = [], 0.0
        if intro_path:
            intro = conform_bookend(intro_path, output_format, target_w, target_h)
            parts.append(intro)
            expected += media_duration(intro)
        parts.append(segment)
        expected += media_duration(segment)
        if outro_path:
            outro = conform_bookend(outro_path, output_format, target_w, target_h)
            parts.append(outro)
            expected += media_duration(outro)

        print(f"[COMPOSE] {len(parts)} segments, expected total {expected:.3f}s",
              flush=True)
        concat_copy(parts, out_path)
        verify_composition(out_path, expected, target_w, target_h)
        return out_path
    except Exception:
        # Never leave a half-composed artifact where the caller might publish it.
        _discard_work_file(out_path)
        raise
    finally:
        for tmp in temporaries:
            _discard_work_file(tmp)


def _discard_work_file(path: Optional[str]) -> None:
    """Remove an unpublished render temp file; never raise from a failure path."""
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
            print(f"[RENDER] Discarded unpublished work file: {path}")
    except OSError as e:
        print(f"[RENDER] Could not remove work file {path}: {e}")


def _normalized_output_path(input_path: str, output_format: str, cache_key: Optional[str]) -> str:
    base, _ext = os.path.splitext(input_path)
    # Content-keyed, not job-keyed: a file named after the job that happened to
    # create it can never be reused by another job. Keeps the *_normalized_*.mp4
    # shape the cleanup sweep globs for.
    safe_key = ''.join(ch for ch in str(cache_key or uuid.uuid4()) if ch.isalnum() or ch in ('-', '_'))[:40]
    return f"{base}_normalized_{output_format}_{safe_key}.mp4"


def _even_dimension(value: float) -> int:
    return max(2, int(round(value / 2.0) * 2))


def _source_video_geometry(input_path: str) -> Dict:
    cmd = [FFPROBE_BIN, '-v', 'quiet', '-print_format', 'json', '-show_streams', input_path]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
    info = json.loads(result.stdout or '{}')
    video_stream = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not video_stream:
        raise ValueError('No video stream found')

    width = int(video_stream.get('width') or 0)
    height = int(video_stream.get('height') or 0)
    if width <= 0 or height <= 0:
        raise ValueError(f'Invalid video dimensions: {width}x{height}')

    fps = 30.0
    rate = video_stream.get('avg_frame_rate') or video_stream.get('r_frame_rate') or ''
    try:
        if '/' in rate:
            num, den = rate.split('/', 1)
            den_f = float(den)
            if den_f:
                fps = float(num) / den_f
        elif rate:
            fps = float(rate)
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 30.0
    if fps <= 0 or fps > 120:
        fps = 30.0

    return {'width': width, 'height': height, 'fps': fps}


def _is_default_reframe(source_edit: Optional[Dict]) -> bool:
    """True when the user has not actually moved anything.

    Used to keep untouched jobs on their existing, proven filter. Any change
    here alters the normalize command, which changes the cache key and forces a
    re-encode of work that was already correct — so "no adjustment" must mean
    "byte-identical to before".
    """
    if not isinstance(source_edit, dict):
        return True
    try:
        return (
            abs(float(source_edit.get('crop_x', 0.5)) - 0.5) < 1e-9
            and abs(float(source_edit.get('crop_y', 0.5)) - 0.5) < 1e-9
            and abs(float(source_edit.get('zoom', 1.0)) - 1.0) < 1e-9
            and str(source_edit.get('crop_mode', 'fit')) == 'fit'
        )
    except (TypeError, ValueError):
        return False


def _build_reframe_filter(input_path: str, source_edit: Optional[Dict],
                          target_w: int = 720, target_h: int = 1280,
                          label: str = 'vertical_9_16') -> Optional[str]:
    """Reframe filter for ANY target size.

    The geometry was never vertical-specific — only the hardcoded 720x1280 was.
    Generalising lets the square path honour crop_x/crop_y/zoom instead of
    ignoring them, which it did silently: a 1:1 user could drag the frame, watch
    the preview move, see the value persist, and get a centred render anyway.
    """
    if not source_edit:
        return None

    crop_mode = source_edit.get('crop_mode', 'fit')
    if crop_mode not in {'fit', 'fill'}:
        print(f"[NORMALIZE-REFRAME] Unsupported crop_mode='{crop_mode}' â€” using legacy center-cover")
        return None

    geom = _source_video_geometry(input_path)
    vw, vh = geom['width'], geom['height']

    def _clamp(value, lo, hi, default):
        try:
            return max(lo, min(hi, float(value)))
        except (TypeError, ValueError):
            return default

    crop_x = _clamp(source_edit.get('crop_x', 0.5), 0.0, 1.0, 0.5)
    crop_y = _clamp(source_edit.get('crop_y', 0.5), 0.0, 1.0, 0.5)
    zoom = _clamp(source_edit.get('zoom', 1.0), 0.25, 4.0, 1.0)

    base_scale = max(target_w / vw, target_h / vh) if crop_mode == 'fill' else min(target_w / vw, target_h / vh)
    scale = base_scale * zoom
    sw = _even_dimension(vw * scale)
    sh = _even_dimension(vh * scale)
    ox = int(round((target_w - sw) * crop_x))
    oy = int(round((target_h - sh) * crop_y))
    fps = geom['fps']

    print(
        f"[NORMALIZE-REFRAME] {label} "
        f"src={vw}x{vh} target={target_w}x{target_h} "
        f"mode={crop_mode} crop=({crop_x:.3f},{crop_y:.3f}) zoom={zoom:.3f} "
        f"scaled={sw}x{sh} overlay=({ox},{oy}) fps={fps:.3f} "
        f"bg={'none' if (sw >= target_w and sh >= target_h) else 'blur-pad'}"
    )

    # Flip mirrors the raw source BEFORE scale/overlay, so pan position is
    # unchanged and only the content is mirrored (matches the canvas preview).
    flip_pre = "hflip," if source_edit.get('flip_h') else ""

    # Background: blur-pad rather than black bars, but ONLY when bars would
    # actually be visible.
    #
    # A landscape source in a 9:16 canvas used to offer a brutal choice: Fit gave
    # video plus a black void, Fill cropped ~68% of the frame width away. A
    # blurred extension of the source itself keeps the whole frame AND fills the
    # canvas, which is what every social tool does. The 1:1 path has done this
    # since it was written (gblur sigma=25) — 9:16 was simply never brought into
    # line, so the two live formats had different Fit philosophies by accident.
    #
    # gblur costs CPU, and CPU is this box's bottleneck, so it is skipped
    # entirely when the scaled source already covers the canvas (Fill, or a
    # source whose aspect already matches). An invisible background is not worth
    # a blur pass.
    covers_canvas = sw >= target_w and sh >= target_h
    if covers_canvas:
        return (
            f"color=c=black:s={target_w}x{target_h}:r={fps:.6f}[base];"
            f"[0:v]{flip_pre}scale={sw}:{sh}[src_scaled];"
            f"[base][src_scaled]overlay={ox}:{oy}:shortest=1[out]"
        )

    # split=2 happens AFTER the flip so the blurred backdrop is mirrored with the
    # foreground; otherwise a flipped video sits on an unflipped ghost of itself.
    return (
        f"[0:v]{flip_pre}split=2[fg][bg_raw];"
        f"[bg_raw]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h}:(iw-{target_w})/2:(ih-{target_h})/2,"
        f"gblur=sigma=25[bg];"
        f"[fg]scale={sw}:{sh}[fg_scaled];"
        f"[bg][fg_scaled]overlay={ox}:{oy}:shortest=1[out]"
    )


def normalize_video(input_path: str, output_format: str = 'vertical_9_16',
                    source_edit: Optional[Dict] = None, job_id: Optional[str] = None) -> str:
    """
    Normalize video to standard 8-bit H264 SDR format, stripping HDR/DOVI metadata,
    and enforce the target output format dimensions.

    This stage MUST run before branding to handle:
    - HEVC 10-bit Dolby Vision inputs
    - Corrupt timestamps from Instagram/TikTok
    - HDR colorspace metadata
    - Format-specific aspect ratio enforcement

    NOTE: As of Patch 24, this function also enforces product output format dimensions
    so that VideoProcessor probes the final target frame size before overlay composition.
    Overlay positions are calculated against post-normalize W×H, so the format transform
    must happen here — not inside VideoProcessor or build_filter_complex.

    Args:
        input_path: Path to the input video file
        output_format: Target output format key (default: 'vertical_9_16')

    Returns:
        Path to normalized video file (or original if normalization fails)
    """
    try:
        # Built against a placeholder so the command can be hashed before the
        # output path exists — the path is derived FROM the hash.
        fixed_path = _OUTPUT_PLACEHOLDER
        print(f"[NORMALIZE] Normalizing video to clean 8-bit H264 SDR: {input_path}")

        NORMALIZE_TIMEOUT = 300  # 5 min — normalization is just scale+re-encode, not overlay rendering

        # Horizontal flip mirrors the raw source before any scale/crop. Applies to
        # ALL formats. For the vertical reframe path it's baked into the reframe
        # filter; for every other path we prepend it here. "" when not flipped.
        flip_pre = "hflip," if (source_edit and source_edit.get('flip_h')) else ""

        if output_format == 'vertical_9_16':
            print(f"[NORMALIZE] output_format=vertical_9_16 target=720x1280")
            reframe_filter = None
            if source_edit:
                try:
                    reframe_filter = _build_reframe_filter(
                        input_path, source_edit, 720, 1280, 'vertical_9_16')
                except Exception as reframe_error:
                    print(
                        "[NORMALIZE-REFRAME WARNING] Failed to build source reframe filter; "
                        f"falling back to legacy center-cover. error={reframe_error}"
                    )

            if reframe_filter:
                cmd = [
                    FFMPEG_BIN, "-y", "-threads", "1", "-i", input_path,
                    "-filter_complex", reframe_filter,
                    "-filter_threads", "1",
                    "-map", "[out]",
                    "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-threads", "1",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart",
                    fixed_path
                ]
            else:
                if source_edit:
                    print("[NORMALIZE-REFRAME WARNING] Source edit present but unused; using legacy center-cover")
                cmd = [
                    FFMPEG_BIN, "-y", "-threads", "1", "-i", input_path,
                    "-vf", flip_pre + "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280:(iw-720)/2:(ih-1280)/2",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-threads", "1",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart",
                    fixed_path
                ]
        elif output_format == 'square_1_1':
            # Honour the user's reframe when they have actually set one. Until
            # now this branch ignored crop_x/crop_y/zoom/crop_mode entirely —
            # only flip_h got through — so a 1:1 drag moved the preview, saved a
            # value, and rendered centred regardless.
            #
            # An UNTOUCHED job deliberately keeps the original hardcoded filter
            # below rather than the generalised one. The two differ by ~1px in
            # the scaled overlay (even-dimension rounding), which is invisible
            # but would change the normalize command, invalidate every cached
            # square entry, and re-encode work that was already correct.
            _sq_reframe = None
            if source_edit and not _is_default_reframe(source_edit):
                _sq_reframe = _build_reframe_filter(
                    input_path, source_edit, 720, 720, 'square_1_1')

            if _sq_reframe:
                _fc = _sq_reframe
                print("[NORMALIZE] output_format=square_1_1 target=720x720 strategy=reframe")
            else:
                # Untouched: the original blur-pad, byte-identical to before.
                # Blurred 720x720 background + foreground scaled to fit, centered.
                # Preserves the full source frame — no cropping of faces/text.
                _fc = (
                    f"[0:v]{flip_pre}split=2[fg][bg_raw];"
                    "[bg_raw]scale=720:720:force_original_aspect_ratio=increase,"
                    "crop=720:720:(iw-720)/2:(ih-720)/2,"
                    "gblur=sigma=25[bg];"
                    "[fg]scale=720:720:force_original_aspect_ratio=decrease[fg_scaled];"
                    "[bg][fg_scaled]overlay=(W-w)/2:(H-h)/2[out]"
                )
                print(f"[NORMALIZE] output_format=square_1_1 target=720x720 strategy=blur-pad")

            cmd = [
                FFMPEG_BIN, "-y", "-threads", "1", "-i", input_path,
                "-filter_complex", _fc,
                "-filter_threads", "1",
                "-map", "[out]",
                "-map", "0:a?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-threads", "1",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                fixed_path
            ]
        elif output_format == 'landscape_16_9':
            # Unlike the 1:1 branch there is no legacy filter to stay
            # byte-identical to and no shipped cache entries to invalidate --
            # this format has never rendered -- so it goes straight through the
            # generalised reframe filter with no special-case duplicate.
            #
            # Default is Fit, matching 9:16 and 1:1: the whole source stays
            # visible and exposed canvas is blur-padded. Fill is reachable, but
            # is not the default, because filling a 16:9 canvas from a vertical
            # source discards roughly two thirds of the frame. That is a choice
            # for the user to make deliberately, not one to make silently on
            # their behalf.
            _ls_edit = source_edit or {
                'crop_x': 0.5, 'crop_y': 0.5, 'zoom': 1.0,
                'crop_mode': 'fit', 'flip_h': 0,
            }
            _ls_reframe = _build_reframe_filter(
                input_path, _ls_edit, 1280, 720, 'landscape_16_9')
            if not _ls_reframe:
                # Unreachable via the API (the resolver clamps crop_mode to
                # fit/fill), but falling through to the width-only fallback
                # would emit a non-1280x720 file that only the dimension check
                # would catch. Fail here instead, where the cause is knowable.
                raise NormalizationError(
                    'could not build landscape_16_9 reframe filter for '
                    f'{input_path} (edit={_ls_edit})')
            print("[NORMALIZE] output_format=landscape_16_9 target=1280x720 strategy=reframe")

            cmd = [
                FFMPEG_BIN, "-y", "-threads", "1", "-i", input_path,
                "-filter_complex", _ls_reframe,
                "-filter_threads", "1",
                "-map", "[out]",
                "-map", "0:a?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-threads", "1",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                fixed_path
            ]
        else:
            # Fallback: width-only normalize, preserve source aspect ratio.
            print(f"[NORMALIZE] output_format={output_format} — using fallback scale=720:-2")
            cmd = [
                FFMPEG_BIN, "-y", "-threads", "1", "-i", input_path,
                "-vf", flip_pre + "scale=720:-2",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-threads", "1",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                fixed_path
            ]

        # ---- cache identity -------------------------------------------------
        cache_key  = _normalize_identity(cmd)          # excludes exe + output path
        fixed_path = _normalized_output_path(input_path, output_format, cache_key)
        # cmd[-1] is still the placeholder here; it is set to the temp path at the
        # point of encoding, so a cache hit never touches it.

        # Fast path: somebody already performed this exact transformation.
        if os.path.isfile(fixed_path):
            print(f"[NORMALIZE CACHE] HIT {cache_key} -> {os.path.basename(fixed_path)}", flush=True)
            return fixed_path

        lock = _lock_for(cache_key)
        if not lock.acquire(blocking=False):
            print(f"[NORMALIZE CACHE] WAIT {cache_key} — another render is producing it", flush=True)
            lock.acquire()
        try:
            # Re-check under the lock: whoever we waited for has now finished.
            if os.path.isfile(fixed_path):
                print(f"[NORMALIZE CACHE] HIT {cache_key} (after wait)", flush=True)
                return fixed_path

            print(f"[NORMALIZE CACHE] MISS {cache_key} — encoding", flush=True)

            # Encode to a temp name, then rename atomically. A half-written file
            # must never be visible at the cache path: another job checking for a
            # HIT would hand FFmpeg a truncated input.
            #
            # The temp MUST keep the .mp4 extension. FFmpeg infers the muxer from
            # the output extension, so a name ending .tmp fails outright with
            # "Unable to find a suitable output format" — and normalize_video then
            # falls back to the un-reframed original, producing a render at the
            # SOURCE aspect ratio that does not match the preview the user approved.
            # Ending in .mp4 does put temps inside the sweep's *_normalized_*.mp4
            # glob, but the sweep is age-based (30 min) and an encode is capped at
            # NORMALIZE_TIMEOUT (5 min), so a temp can never grow old enough to be
            # swept while it is still being written.
            _stem, _ext = os.path.splitext(fixed_path)
            tmp_path = f"{_stem}.{_uuid.uuid4().hex}.tmp{_ext or '.mp4'}"
            cmd[-1]  = tmp_path
            run_cmd  = NICE_PREFIX + cmd

            print(f"[NORMALIZE] Running command (timeout={NORMALIZE_TIMEOUT}s): {' '.join(run_cmd)}")
            result = subprocess.run(
                run_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=NORMALIZE_TIMEOUT
            )

            if result.returncode == 0 and os.path.exists(tmp_path):
                os.replace(tmp_path, fixed_path)       # atomic publish
                file_size = os.path.getsize(fixed_path) / (1024 * 1024)
                print(f"[NORMALIZE] Successfully normalized video: {fixed_path} ({file_size:.2f}MB)")
                return fixed_path
            else:
                print(f"[NORMALIZE] Failed to normalize video (code={result.returncode}). stderr: {(result.stderr or '')[-1000:]}")
                if output_format == 'vertical_9_16' and source_edit:
                    print(
                        "[NORMALIZE-REFRAME WARNING] Source reframe normalization failed; "
                        "falling back to original input, so render may not match preview."
                    )
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)                # never publish a failed encode
                # FATAL. Returning input_path here used to hand the brand render an
                # un-reframed source at the WRONG aspect ratio, which then rendered
                # "successfully" and charged a credit. Fail loudly instead.
                raise NormalizationError(
                    f'normalize failed (code={result.returncode}) for {output_format}: '
                    f'{(result.stderr or "")[-300:]}'
                )
        finally:
            lock.release()
    except NormalizationError:
        raise
    except subprocess.TimeoutExpired:
        raise NormalizationError(
            f'normalization timed out after {NORMALIZE_TIMEOUT}s for {output_format}')
    except Exception as e:
        raise NormalizationError(f'normalization error for {output_format}: {e}')


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

    def _validate_output(self, output_path: str) -> bool:
        """
        Validate a freshly-rendered output file by probing it directly.

        A render is accepted only when the file is actually playable, not merely
        present. FFmpeg can exit non-zero (e.g. an audio-muxer hiccup) yet still
        write a complete branded video; it can also leave a truncated/empty file
        behind. This guards both cases by confirming: the file exists, is
        non-empty, ffprobe finds a video stream, and the container reports a
        positive duration.

        Args:
            output_path: Path to the rendered file to validate.

        Returns:
            bool: True if the output is a valid, non-empty video file.
        """
        try:
            if not os.path.exists(output_path):
                print(f"[VALIDATE] Reject: output missing — {output_path}")
                return False

            size = os.path.getsize(output_path)
            if size == 0:
                print(f"[VALIDATE] Reject: output is 0 bytes — {output_path}")
                return False

            cmd = [FFPROBE_BIN, '-v', 'quiet', '-print_format', 'json',
                   '-show_format', '-show_streams', output_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                print(f"[VALIDATE] Reject: ffprobe failed (code={result.returncode}) — {output_path}")
                return False

            info = json.loads(result.stdout or '{}')
            streams = info.get('streams', [])

            has_video = any(s.get('codec_type') == 'video' for s in streams)
            if not has_video:
                print(f"[VALIDATE] Reject: no video stream — {output_path}")
                return False

            # Duration may sit on the container format or on the video stream,
            # depending on the muxer — check both before giving up.
            def _as_float(val):
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return 0.0

            duration = _as_float(info.get('format', {}).get('duration'))
            if duration <= 0:
                for s in streams:
                    if s.get('codec_type') == 'video':
                        duration = _as_float(s.get('duration'))
                        break
            if duration <= 0:
                print(f"[VALIDATE] Reject: non-positive duration ({duration}) — {output_path}")
                return False

            print(f"[VALIDATE] OK: {size//1024}KB, duration={duration:.1f}s, "
                  f"video stream present — {output_path}")
            return True

        except subprocess.TimeoutExpired:
            print(f"[VALIDATE] Reject: ffprobe timed out — {output_path}")
            return False
        except Exception as e:
            print(f"[VALIDATE] Reject: validation error: {e} — {output_path}")
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
        
        # 1. Try DB-stored logo_path (SaaS model — always takes priority)
        if brand_config:
            db_path = brand_config.get('logo_path')
            if db_path:
                # Path is relative to STORAGE_ROOT
                full_path = os.path.join(STORAGE_ROOT, db_path)
                if os.path.exists(full_path):
                    print(f"[LOGO] Using uploaded logo: {full_path}")
                    return full_path
                # logo_path is set in DB but file is missing (ephemeral storage cleared after redeploy?)
                # Do NOT fall through to Circle folder — that would silently use a stale/wrong logo.
                print(f"[LOGO WARNING] resolve_logo_path: DB logo_path set but file missing on disk.")
                print(f"[LOGO WARNING]   brand='{brand_name}'  logo_path='{db_path}'  expected='{full_path}'")
                print(f"[LOGO WARNING]   Returning None — re-upload the logo to restore it.")
                return None
            # brand_config present but no logo_path in DB
            if brand_config.get('user_id'):
                # User-owned brand: no logo uploaded yet, don't fall back to master Circle assets
                print(f"[LOGO] resolve_logo_path: user brand '{brand_name}' has no logo_path in DB — skipping Circle fallback")
                return None

        # 2. Fallback to master assets Circle folder (system/legacy brands only, no user_id in config)
        logo_filename = f"{brand_name}_logo.png"
        path = os.path.join(self.LOGOS_DIR, logo_filename)

        print(f"[LOGO] Looking for legacy system logo: {path}")

        if os.path.exists(path):
            print(f"[LOGO] Found legacy system logo: {path}")
            return path
        else:
            print(f"[LOGO ERROR] Logo not found for brand '{brand_name}'")
            print(f"[LOGO ERROR] Expected: {logo_filename}")
            print(f"[LOGO ERROR] All legacy logos must follow pattern: {{BrandName}}_logo.png")
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
        
        wm_mode_raw = brand_config.get('wm_mode', 'positioned')
        # Normalize stale 'fullscreen' values — positioned is the only supported mode
        if wm_mode_raw != 'positioned':
            print(f"[WM MODE] Normalized wm_mode from '{wm_mode_raw}' to 'positioned' for brand='{brand_name}'")
        wm_mode = 'positioned'
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

        # If x/y are still at migration defaults, translate text_position into y_pct.
        # Brands with explicit canvas placement have non-default values and are unaffected.
        if abs(text_x_pct - 0.5) < 0.001 and abs(text_y_pct - 0.2) < 0.001:
            text_position = brand_config.get('text_position', 'top')
            if text_position == 'bottom':
                text_y_pct = 0.88
            elif text_position == 'lower-third':
                text_y_pct = 0.72
            elif text_position == 'center':
                text_y_pct = 0.5
            # 'top' keeps 0.2 — correct as-is
            print(f"[VISUAL_PRESET] Text position fallback: '{text_position}' → y={text_y_pct:.2f}")

        print(f"[VISUAL_PRESET] Logo: x={logo_x_pct:.2f}, y={logo_y_pct:.2f}, scale={logo_scale_pct:.2f}, opacity={logo_opacity:.2f}, rotation={logo_rotation}°")
        print(f"[VISUAL_PRESET] Watermark: mode={wm_mode}, x={wm_x_pct:.2f}, y={wm_y_pct:.2f}, scale={wm_scale_pct:.2f}, opacity={wm_opacity:.2f}")
        print(f"[WM RENDER] brand='{brand_name}' wm_mode={wm_mode} wm_x={wm_x_pct:.4f} wm_y={wm_y_pct:.4f} wm_scale={wm_scale_pct:.4f} wm_opacity={wm_opacity:.4f}")
        print(f"[VISUAL_PRESET] Text: enabled={text_enabled}, content='{text_content[:30]}', x={text_x_pct:.2f}, y={text_y_pct:.2f}")

        # 1. WATERMARK OVERLAY
        watermark_path = self.resolve_watermark_path(brand_name, brand_config)
        if watermark_path:
            print(f"[VISUAL_PRESET] Adding watermark: {watermark_path}")

            # Always positioned — fullscreen removed as user-facing option.
            # wm_scale_pct is render-domain (UI%/100 × 1.15).
            # Divide by WM_UI_REF_SCALE to recover raw UI%/100 so the rendered
            # size matches the Brand Editor preview formula exactly.
            WM_UI_REF_SCALE = 1.15
            ui_scale = wm_scale_pct / WM_UI_REF_SCALE
            wm_target_w = int(ui_scale * W * 0.5)
            wm_cx_px = int(wm_x_pct * W)
            wm_cy_px = int(wm_y_pct * H)
            wm_x_expr = f"{wm_cx_px}-w/2"
            wm_y_expr = f"{wm_cy_px}-h/2"

            print(f"[VISUAL_PRESET] Watermark positioned: width={wm_target_w}px, center=({wm_cx_px},{wm_cy_px}), opacity={wm_opacity:.2f}")
            print(f"[WM RENDER] computed size={wm_target_w}x(auto) overlay={wm_x_expr},{wm_y_expr}")

            filters.append(f"movie='{watermark_path}',scale={wm_target_w}:-1,format=rgba,colorchannelmixer=aa={wm_opacity}[watermark]")
            filters.append(f"[{current_input}][watermark]overlay={wm_x_expr}:{wm_y_expr}[v1]")
            current_input = 'v1'
        else:
            print(f"[VISUAL_PRESET] No watermark found, skipping")
        
        # 2. LOGO OVERLAY
        logo_path = self.resolve_logo_path(brand_name, brand_config)
        if logo_path:
            logo_target_w = int(logo_scale_pct * W)
            logo_cx_px = int(logo_x_pct * W)
            logo_cy_px = int(logo_y_pct * H)
            # Use FFmpeg overlay expressions so 'h' resolves to the actual scaled logo height,
            # correctly centering non-square logos (previously used logo_target_w for Y offset).
            logo_x_expr = f"{logo_cx_px}-w/2"
            logo_y_expr = f"{logo_cy_px}-h/2"

            logo_shape = brand_config.get('logo_shape') or 'original'
            print(f"[VISUAL_PRESET] Adding logo: {logo_path}")
            print(f"[VISUAL_PRESET] Logo: width={logo_target_w}px, center=({logo_cx_px},{logo_cy_px}), opacity={logo_opacity:.2f}, rotation={logo_rotation}°")
            print(f"[SHAPE] render brand='{brand_name}' logo_shape='{logo_shape}'")

            # Geq filter for circle crop — masks pixels outside the inscribed circle
            geq_circle = (
                "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)'"
                ":a='if(lte((X-W/2)*(X-W/2)+(Y-H/2)*(Y-H/2),(min(W\\,H)/2)*(min(W\\,H)/2)),alpha(X\\,Y),0)'"
            )

            # Build logo filter with optional rotation
            if logo_rotation != 0.0:
                # Convert degrees to radians for FFmpeg rotate filter
                rotation_rad = (logo_rotation * 3.14159265359) / 180.0
                print(f"[VISUAL_PRESET] Applying rotation: {logo_rotation}° = {rotation_rad:.4f} radians")

                # Apply scale -> rotate -> shape -> opacity in sequence
                shape_filter = f",{geq_circle}" if logo_shape == 'circle' else ''
                filters.append(f"movie='{logo_path}',scale={logo_target_w}:-1,format=rgba,rotate={rotation_rad}:ow=hypot(iw,ih):oh=ow:fillcolor=0x00000000{shape_filter}[logo_rotated]")
                filters.append(f"[logo_rotated]colorchannelmixer=aa={logo_opacity}[logo]")
            else:
                # No rotation - simple path; apply shape before opacity
                shape_filter = f",{geq_circle}" if logo_shape == 'circle' else ''
                filters.append(f"movie='{logo_path}',scale={logo_target_w}:-1,format=rgba{shape_filter},colorchannelmixer=aa={logo_opacity}[logo]")

            filters.append(f"[{current_input}][logo]overlay={logo_x_expr}:{logo_y_expr}[v2]")
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
            sec_cx_px = int(sec_x_pct * W)
            sec_cy_px = int(sec_y_pct * H)
            sec_x_expr = f"{sec_cx_px}-w/2"
            sec_y_expr = f"{sec_cy_px}-h/2"

            print(f"[VISUAL_PRESET] Adding secondary logo: {sec_logo_path}")
            print(f"[VISUAL_PRESET] SecLogo: width={sec_target_w}px, center=({sec_cx_px},{sec_cy_px}), opacity={sec_opacity:.2f}, rotation={sec_rotation}°")
            
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
            
            filters.append(f"[{current_input}][sec_logo]overlay={sec_x_expr}:{sec_y_expr}[{next_v}]")
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
            
            # L7: derive next label by incrementing current rather than hardcoding
            # — prevents collision when secondary logo already occupied v3 or v4
            if current_input == '0:v':
                next_label = 'v1'
            elif current_input.startswith('v') and current_input[1:].isdigit():
                next_label = f'v{int(current_input[1:]) + 1}'
            else:
                next_label = 'v1'
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
            overlay_x = -offset_x
            overlay_y = -offset_y
            opacity = self.WATERMARK_OPACITY
            filters.append(f"movie='{watermark_path}',scale={scaled_width}:{scaled_height},format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{opacity}*alpha(X,Y)'[watermark]")
            filters.append(f"[{current_input}][watermark]overlay={overlay_x}:{overlay_y}[v1]")
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
            
            # L7: incremental label — same fix as build_filter_complex_visual
            if current_input == '0:v':
                next_label = 'v1'
            elif current_input.startswith('v') and current_input[1:].isdigit():
                next_label = f'v{int(current_input[1:]) + 1}'
            else:
                next_label = 'v1'
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
                     video_id: str = 'video', output_format: str = 'vertical_9_16',
                     intro_path: Optional[str] = None,
                     outro_path: Optional[str] = None) -> str:
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
        output_filename = f"{video_id}_{brand_name}_{output_format}.mp4"
        output_path = os.path.join(self.output_dir, output_filename)
        
        print(f"[DEBUG] Processing brand: {brand_name}")
        print(f"[DEBUG] Video ID: {video_id}")
        print(f"[DEBUG] Output format: {output_format}")
        print(f"[DEBUG] Output filename: {output_filename}")
        print(f"[DEBUG] Output path: {output_path}")
        
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Render to a UNIQUE work path, publish atomically at the end.
        #
        # output_filename is deterministic — {video_id}_{brand}_{format}.mp4 —
        # which is right for the DELIVERED artifact but wrong as a working path.
        # Two concurrent renders of the same (video, brand, format) both opened
        # this file with -y and interleaved their writes. _validate_output then
        # probed a file the other job was still writing, so a render could be
        # certified, recorded in branded_outputs and charged a credit while its
        # bytes were still changing underneath. That is the same class of defect
        # as the 31 Aug incident: an internal assumption -- "the file I validated
        # is the file that persists" -- masquerading as success.
        #
        # normalize_video has published atomically since 31 Aug; the brand render
        # never did. Concurrent jobs now each build their own file and the last
        # publish wins WHOLE, so the delivered artifact is always a complete,
        # validated encode rather than a mix of two.
        #
        # The extension MUST survive into the temp name. FFmpeg infers its muxer
        # from it, and a path ending '.tmp' fails outright -- that exact mistake
        # silently produced a landscape file for a vertical request on 31 Aug.
        _out_stem, _out_ext = os.path.splitext(output_path)
        work_path = f"{_out_stem}.{_uuid.uuid4().hex}.tmp{_out_ext or '.mp4'}"
        print(f"[DEBUG] Writing branded video to: {work_path}")
        print(f"[DEBUG] Will publish to: {output_path}")
        
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
        
        # Build FFmpeg command — veryfast preset keeps encoding time within request window
        # (fast preset can take 5-10+ min on shared CPU for long videos, causing gunicorn timeout)
        FFMPEG_TIMEOUT = 840  # 14 minutes — raises clean Python error before gunicorn 900s kill

        # Video-only base. The brand step never touches audio (filter_complex is video-only),
        # so audio codec/flags are appended per-attempt below.
        base_cmd = [
            FFMPEG_BIN, '-y',
            '-i', self.video_path,
            '-filter_complex', filter_complex,
            '-threads', '1',
            '-filter_threads', '1',
            '-map', '[vout]',
            '-map', '0:a?',
            '-c:v', 'libx264',
            '-crf', '23',
            '-preset', 'veryfast',   # was 'fast' — ~2x faster, fits within request window
        ]
        tail_cmd = ['-movflags', '+faststart', work_path]

        # Audio strategy tiers. The input is already normalized to clean AAC 128k upstream
        # (see normalize_video), so a stream copy is both higher quality and avoids the AAC
        # re-encoder failures (FFmpeg exit 69 / "Conversion failed!") that have discarded
        # otherwise-complete renders. Fall back to a resync'd re-encode for the rare case
        # where the audio isn't mp4-copyable, then drop audio only as a last resort so a
        # render never fails outright.
        audio_attempts = [
            ('copy',       ['-c:a', 'copy']),
            ('reencode',   ['-c:a', 'aac', '-b:a', '128k', '-af', 'aresample=async=1:first_pts=0']),
            ('drop-audio', ['-an']),
        ]

        last_error = ''
        for attempt_idx, (label, audio_flags) in enumerate(audio_attempts, 1):
            cmd = NICE_PREFIX + base_cmd + audio_flags + tail_cmd
            print(f"[RENDER] Starting FFmpeg for brand='{brand_name}' "
                  f"(audio={label}, attempt {attempt_idx}/{len(audio_attempts)})")
            print(f"[RENDER] Input:   {self.video_path}")
            print(f"[RENDER] Output:  {output_path}")
            print(f"[RENDER] Timeout: {FFMPEG_TIMEOUT}s")
            print(f"[RENDER] Command: {' '.join(cmd)}")

            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,  # FFmpeg has no useful stdout
                    stderr=subprocess.PIPE,     # Capture stderr for error reporting only
                    text=True,
                    timeout=FFMPEG_TIMEOUT
                )
            except subprocess.TimeoutExpired:
                processing_time = time.time() - start_time
                print(f"[RENDER ERROR] FFmpeg timed out after {processing_time:.0f}s for brand='{brand_name}'")
                print(f"[RENDER ERROR] Work path: {work_path}")
                _discard_work_file(work_path)
                raise Exception(
                    f"FFmpeg timed out after {FFMPEG_TIMEOUT//60} minutes for brand '{brand_name}'. "
                    f"Try a shorter clip (under 60 seconds)."
                )

            processing_time = time.time() - start_time
            # Validate the file THIS job wrote, not the shared destination.
            output_valid = self._validate_output(work_path)
            output_size = os.path.getsize(work_path) if os.path.exists(work_path) else 0
            print(f"[RENDER] FFmpeg returned code={result.returncode} in {processing_time:.1f}s (audio={label})")
            print(f"[RENDER] Output valid={output_valid} size={output_size} bytes")

            if output_valid:
                # Accept the render if the file probes clean, even when FFmpeg reported a
                # non-zero exit (e.g. an audio-muxer hiccup) — the branded video is complete.
                if result.returncode != 0:
                    print(f"[RENDER WARN] FFmpeg exit={result.returncode} but output probes valid — "
                          f"accepting (audio={label})")
                if label == 'drop-audio':
                    print(f"[RENDER WARN] brand='{brand_name}' rendered WITHOUT audio "
                          f"after audio copy + re-encode both failed")
                # Intro/outro composition, if any. It happens HERE -- after the
                # branded encode has probed clean, before validation of the final
                # artifact and before publication -- because composing after the
                # publish would mutate a delivered file, which is exactly the
                # defect the work-path change closed.
                #
                # Target dimensions come from MEASURING the branded output rather
                # than from EXPECTED_OUTPUT_DIMS, so a bookend is conformed to
                # what was actually produced instead of what was requested.
                publish_path = work_path
                composed_path = None
                if intro_path or outro_path:
                    _bw, _bh = probe_dimensions(work_path)
                    if not _bw or not _bh:
                        _discard_work_file(work_path)
                        raise CompositionError(
                            'cannot compose: branded output dimensions unreadable')
                    composed_path = (f"{os.path.splitext(work_path)[0]}"
                                     f".composed.{_uuid.uuid4().hex}.tmp.mp4")
                    try:
                        compose_bookends(work_path, composed_path, output_format,
                                         _bw, _bh, intro_path, outro_path)
                    except Exception:
                        # compose_bookends already removed its own output; the
                        # branded intermediate is ours to clean up.
                        _discard_work_file(work_path)
                        raise
                    publish_path = composed_path

                # Atomic publish: os.replace is atomic within a filesystem, so a
                # reader either sees the previous file or this one, never a blend.
                #
                # Guarded because a failure here would otherwise strand the work
                # file forever: sweep_normalized_temp_files only globs
                # '*_normalized_*' inside RAW_DIR, and these live in OUTPUT_DIR
                # under a different name, so nothing would ever collect them on a
                # 5 GB disk. (POSIX rename(2) does not fail this way, but the
                # render must not depend on that to avoid leaking storage.)
                try:
                    os.replace(publish_path, output_path)
                except OSError as publish_error:
                    _discard_work_file(publish_path)
                    if composed_path:
                        _discard_work_file(work_path)
                    raise Exception(
                        f"render succeeded but publishing failed for brand "
                        f"'{brand_name}': {publish_error}"
                    ) from publish_error
                if composed_path:
                    # The branded intermediate has served its purpose.
                    _discard_work_file(work_path)
                print(f"[RENDER] Published {publish_path} -> {output_path}")
                print(f"[RENDER] Completed brand='{brand_name}' in {processing_time:.1f}s "
                      f"({output_size//1024}KB, audio={label})")
                return output_path

            last_error = (result.stderr or '')[-1500:]
            print(f"[RENDER ERROR] Attempt {attempt_idx} (audio={label}) failed for "
                  f"brand='{brand_name}' code={result.returncode}")
            print(f"[RENDER ERROR] stderr tail: {last_error}")

        # All audio strategies exhausted and the output never probed valid — the failure
        # is not audio-related (bad filter, missing input, disk, etc.). Nothing is
        # published, so the previously delivered artifact (if any) is left intact.
        _discard_work_file(work_path)
        raise Exception(
            f"FFmpeg error for brand '{brand_name}' after {len(audio_attempts)} attempts: {last_error}"
        )
    
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
