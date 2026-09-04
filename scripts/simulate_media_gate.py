"""A file that can never render must not become a Library entry.

THE GAP. /api/videos/upload validated the EXTENSION and nothing else. Anything
renamed to .mp4 was saved to RAW_DIR, given a downloads row, and appeared in the
Library as selectable. No credit was ever lost -- normalization failure has been
fatal since ce8da46 -- but the user found out minutes later, inside a render,
with "render failed" and no reason.

probe_media() is a CONTAINER/STREAM gate, deliberately not a frame decode.
Decoding is expensive and the render stage already owns it. This only rejects
files that could never have rendered.

Part 1 runs the REAL probe_media, AST-extracted from video_processor.py and
executed against a stubbed ffprobe, so every branch is reachable deterministically
and the suite stays FFmpeg-free like the rest. Part 2 asserts both routes into
the Library are gated, and gated in the right order.

    python scripts/simulate_media_gate.py
"""
import ast
import io
import json
import os
import subprocess
import tempfile

VP = io.open(os.path.join('portal', 'video_processor.py'), encoding='utf-8').read()
APP = io.open(os.path.join('portal', 'app.py'), encoding='utf-8').read()

PASS = 0


def ok(label, extra=''):
    global PASS
    PASS += 1
    print('  ok  %-54s %s' % (label, extra))


# --- load the real probe against a stubbed ffprobe ---------------------------
class FakeCompleted(object):
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class FakeSubprocess(object):
    """Stands in for the subprocess module inside probe_media."""
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self):
        self.behaviour = FakeCompleted(0, '{}')
        self.calls = []

    def run(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return self.behaviour


fake = FakeSubprocess()
ns = {'os': os, 'json': json, 'subprocess': fake, 'FFPROBE_BIN': 'ffprobe'}
for node in ast.parse(VP).body:
    nm = None
    if isinstance(node, ast.FunctionDef):
        nm = node.name
    elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        nm = node.targets[0].id
    if nm in ('MEDIA_PROBE_TIMEOUT', 'probe_media'):
        exec(ast.get_source_segment(VP, node), ns)

probe_media = ns['probe_media']
TIMEOUT = ns['MEDIA_PROBE_TIMEOUT']
print('probe timeout : %ds' % TIMEOUT)

work = tempfile.mkdtemp(prefix='brandr_gate_')
real_file = os.path.join(work, 'clip.mp4')
io.open(real_file, 'wb').write(b'\x00' * 4096)
empty_file = os.path.join(work, 'empty.mp4')
io.open(empty_file, 'wb').write(b'')


def ffprobe_says(streams, fmt=None, returncode=0):
    fake.behaviour = FakeCompleted(
        returncode, json.dumps({'streams': streams, 'format': fmt or {}}))


VIDEO = {'codec_type': 'video', 'codec_name': 'h264', 'width': 1920, 'height': 1080}
AUDIO = {'codec_type': 'audio', 'codec_name': 'aac'}

print('\n[a real video is admitted]')
ffprobe_says([VIDEO, AUDIO], {'duration': '40.01'})
good, reason, meta = probe_media(real_file)
assert good and reason == '', (good, reason)
ok('valid mp4 accepted', '%dx%d %.2fs' % (meta['width'], meta['height'], meta['duration']))
assert meta == {'width': 1920, 'height': 1080, 'duration': 40.01,
                'codec': 'h264', 'has_audio': True}, meta
ok('metadata is reported for the caller', 'codec + audio presence')

ffprobe_says([VIDEO], {'duration': '12'})
good, _, meta = probe_media(real_file)
assert good and meta['has_audio'] is False
ok('a silent video is still valid', 'has_audio=False, not a rejection')

# Some containers carry duration only on the stream, some only on the format.
ffprobe_says([dict(VIDEO, duration='9.5')], {})
good, _, meta = probe_media(real_file)
assert good and meta['duration'] == 9.5
ok('duration may come from the stream instead of the format')

print('\n[files that could never render are refused]')
ffprobe_says([AUDIO], {'duration': '30'})
good, reason, _ = probe_media(real_file)
assert not good and 'no video track' in reason, reason
ok('audio-only file rejected', reason)

# An MP3 with cover art HAS a video stream -- a still image. Without the
# attached_pic exclusion, renaming that to .mp4 would pass this gate.
ffprobe_says([{'codec_type': 'video', 'codec_name': 'mjpeg', 'width': 600,
               'height': 600, 'disposition': {'attached_pic': 1}}, AUDIO],
             {'duration': '180'})
good, reason, _ = probe_media(real_file)
assert not good and 'no video track' in reason, reason
ok('audio file with cover art rejected', 'attached_pic is not footage')

ffprobe_says([{'codec_type': 'video', 'codec_name': 'h264', 'width': 0, 'height': 0}],
             {'duration': '10'})
good, reason, _ = probe_media(real_file)
assert not good and 'dimensions' in reason, reason
ok('zero dimensions rejected', reason)

ffprobe_says([dict(VIDEO, width=None, height=None)], {'duration': '10'})
good, reason, _ = probe_media(real_file)
assert not good and 'dimensions' in reason
ok('missing dimensions rejected')

ffprobe_says([VIDEO], {'duration': '0'})
good, reason, _ = probe_media(real_file)
assert not good and 'duration' in reason, reason
ok('zero duration rejected', reason)

ffprobe_says([VIDEO], {})
good, reason, _ = probe_media(real_file)
assert not good and 'duration' in reason
ok('absent duration rejected', 'N/A is not "probably fine"')

print('\n[a renamed non-media file: ffprobe simply fails]')
fake.behaviour = FakeCompleted(1, '', 'moov atom not found')
good, reason, _ = probe_media(real_file)
assert not good and 'not a readable video file' in reason, reason
ok('ffprobe non-zero exit rejected', reason)

fake.behaviour = FakeCompleted(0, 'not json at all')
good, reason, _ = probe_media(real_file)
assert not good and 'not a readable video file' in reason
ok('unparseable ffprobe output rejected')

print('\n[probe failure modes reject CLEANLY rather than raising]')
fake.behaviour = subprocess.TimeoutExpired(cmd='ffprobe', timeout=TIMEOUT)
good, reason, _ = probe_media(real_file)
assert not good and str(TIMEOUT) in reason, reason
ok('ffprobe timeout rejected cleanly', reason)

fake.behaviour = OSError('ffprobe not found')
good, reason, _ = probe_media(real_file)
assert not good and 'could not be inspected' in reason
ok('ffprobe missing/erroring rejected cleanly', 'no traceback escapes')

print('\n[cheap checks happen before ffprobe is spawned]')
before = len(fake.calls)
good, reason, _ = probe_media(empty_file)
assert not good and 'empty' in reason, reason
assert len(fake.calls) == before, 'ffprobe was spawned for a 0-byte file'
ok('empty file rejected without spawning ffprobe')

good, reason, _ = probe_media(os.path.join(work, 'nope.mp4'))
assert not good and 'could not be found' in reason, reason
assert len(fake.calls) == before
ok('missing file rejected without spawning ffprobe')

fake.behaviour = FakeCompleted(0, json.dumps({'streams': [VIDEO],
                                              'format': {'duration': '5'}}))
probe_media(real_file, timeout=7)
assert fake.calls[-1][1].get('timeout') == 7
ok('the probe is always bounded by a timeout', 'single-worker box')


# --- part 2: both routes into the Library are gated -------------------------
print('\n[every route into the Library is gated, in the right order]')

assert APP.count('save_download(') == 2, APP.count('save_download(')
ok('there are exactly two ways into the Library', 'both must be covered')

upload = APP[APP.index('def upload_video():'):APP.index("@app.route('/api/downloads/<int:download_id>/rename'")]
i_probe = upload.index('probe_media(file_path)')
i_save = upload.index('save_download(')
assert i_probe < i_save, 'upload saves the record before probing'
ok('upload probes BEFORE creating the downloads row')
assert "return jsonify({" in upload[i_probe:i_save]
ok('a rejected upload returns before reaching save_download',
   'it can never become a renderable job')
reject = upload[i_probe:i_save]
assert 'os.remove(file_path)' in reject
ok('a rejected upload is deleted', 'nothing stranded on a 5 GB disk')
assert "'code': 'INVALID_MEDIA'" in reject and '400' in reject
ok('rejection is a 400 with a machine-readable code')
# The uploader renders `data.error || 'Upload failed'`, so that field must hold
# the sentence. An earlier version put the CODE there and would have shown the
# user the literal string "INVALID_MEDIA".
assert "'error': f\"Sorry" in reject
ok('the human sentence is in `error`, where the UI reads it',
   'matches the existing convention on this route')

savedl = APP[APP.index('def save_video_download():'):APP.index('def upload_video():')]
i_probe2 = savedl.index('probe_media(real_file_path)')
i_save2 = savedl.index('save_download(')
assert i_probe2 < i_save2
ok('save-download probes BEFORE creating the downloads row')
# This file was NOT created by that request, so a probe failure must not delete
# someone else's media. Refuse the record, keep the file.
reject2 = savedl[i_probe2:i_save2]
assert 'os.remove' not in reject2
ok('save-download rejects the RECORD, never deletes the file',
   'it did not create that file')

# Neither route touches credits, so a rejection cannot cost anything.
assert 'spend_credits' not in upload and 'spend_credits' not in savedl
ok('neither route touches credits', 'a rejected file cannot cost anything')

print('\n%d assertions passed.' % PASS)
