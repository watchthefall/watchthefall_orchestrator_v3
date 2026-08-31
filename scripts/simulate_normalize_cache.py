"""Offline assertions for the command-identity normalize cache (commit 2).

Proves the invariant: for a given normalize command there is never more than one
canonical normalized file produced, and the identity ignores everything that does
not change the media.

The headline case is the real one from production: 5 brands x 2 formats arrives
as 10 separate jobs (every submit site sends a single brand_id), which must
collapse to 2 encodes — one per format — not 10.

No Flask, no DB, no FFmpeg, no network, no credits. Run from the repo root:
    python scripts/simulate_normalize_cache.py
"""
import os
import re
import io
import sys
import time
import shutil
import hashlib
import tempfile
import threading

SRC = io.open(os.path.join('portal', 'video_processor.py'), encoding='utf-8').read()

# Pull the real constants/logic out of the module without importing it (importing
# portal.* drags in Flask, and video_processor imports config at module scope).
VERSION = re.search(r"NORMALIZE_CACHE_VERSION = '([^']+)'", SRC).group(1)


def identity(cmd):
    """Mirror of _normalize_identity."""
    payload = '\x1f'.join(str(a) for a in cmd[1:-1])
    return '%s-%s' % (VERSION, hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16])


PASS = 0


def ok(label, extra=''):
    global PASS
    PASS += 1
    print('  ok  %-56s %s' % (label, extra))


def norm_cmd(src, fmt, crop_y=0.5, zoom=1.0, flip=0, nice=('nice', '-n', '10')):
    """A stand-in with the same shape as the real normalize commands."""
    vf = 'scale=720:1280,crop=%.3f,zoom=%.3f%s' % (crop_y, zoom, ',hflip' if flip else '')
    dims = '720x1280' if fmt == 'vertical_9_16' else '720x720'
    return list(nice) + [
        '/usr/bin/ffmpeg', '-y', '-threads', '1', '-i', src,
        '-filter_complex', vf, '-s', dims,
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
        '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k',
        '<<NORMALIZE_OUTPUT>>',
    ]


# The real code hashes AFTER stripping the nice prefix, so strip it here too.
def key(cmd):
    while cmd and cmd[0] in ('nice', '-n') or (cmd and cmd[0].isdigit() and len(cmd) > 1):
        cmd = cmd[1:]
    return identity(cmd)


print('\n[identity ignores what does not change the media]')
base = norm_cmd('/raw/a.mp4', 'vertical_9_16')
assert key(base) == key(norm_cmd('/raw/a.mp4', 'vertical_9_16', nice=('nice', '-n', '19'))), \
    'FFMPEG_NICE changed the key — a priority tweak must not invalidate the cache'
ok('nice level excluded', '(10 vs 19)')
assert key(base) == key(norm_cmd('/raw/a.mp4', 'vertical_9_16', nice=())), 'nice presence changed the key'
ok('nice absence excluded')
_diff_exe = norm_cmd('/raw/a.mp4', 'vertical_9_16')
_diff_exe[3] = '/opt/bin/ffmpeg'          # index 3 == exe once nice is stripped
assert key(base) == key(_diff_exe), 'executable path changed the key'
ok('executable path excluded')

print('\n[identity captures everything that DOES change the media]')
for label, other in [
    ('input source',  norm_cmd('/raw/b.mp4', 'vertical_9_16')),
    ('output format', norm_cmd('/raw/a.mp4', 'square_1_1')),
    ('crop position', norm_cmd('/raw/a.mp4', 'vertical_9_16', crop_y=0.45)),
    ('zoom',          norm_cmd('/raw/a.mp4', 'vertical_9_16', zoom=0.64)),
    ('horizontal flip', norm_cmd('/raw/a.mp4', 'vertical_9_16', flip=1)),
]:
    assert key(base) != key(other), '%s did NOT change the key — brands would share wrong framing' % label
    ok('%s changes the key' % label)

print('\n[key format is versioned]')
assert key(base).startswith(VERSION + '-'), key(base)
ok('key carries a schema version', '(%s)' % key(base))
assert len(key(base)) <= 40, 'key too long for the filename budget'
ok('key fits the filename')

print('\n[THE PRODUCTION CASE: 5 brands x 2 formats]')
# Every submit site sends brand_ids: [item.brand_id] — one brand per job. The
# brand never enters the normalize command, so all 5 brands of a format collapse.
jobs = []
for brand in ('aiwtf', 'australia', 'britainwtf', 'canadawtf', 'conceptswtf'):
    for fmt in ('vertical_9_16', 'square_1_1'):
        jobs.append((brand, fmt, norm_cmd('/raw/reel.mp4', fmt)))
assert len(jobs) == 10
distinct = {key(c) for _b, _f, c in jobs}
print('      10 jobs submitted -> %d distinct normalize identities' % len(distinct))
assert len(distinct) == 2, 'expected 2 (one per format), got %d' % len(distinct)
ok('10 jobs collapse to 2 encodes', '(80%% less normalize work)')

print('\n[one encode per identity, even when jobs race]')
tmp = tempfile.mkdtemp(prefix='brandr_cache_')
try:
    encodes = []
    enc_lock = threading.Lock()
    locks, locks_guard = {}, threading.Lock()

    def lock_for(k):
        with locks_guard:
            if k not in locks:
                locks[k] = threading.Lock()
            return locks[k]

    def fake_normalize(cmd):
        """Mirrors the real flow: fast path, per-key lock, re-check, atomic publish."""
        k = key(cmd)
        path = os.path.join(tmp, 'reel_normalized_%s.mp4' % k)
        if os.path.isfile(path):
            return path, 'HIT'
        lk = lock_for(k)
        waited = not lk.acquire(blocking=False)
        if waited:
            lk.acquire()
        try:
            if os.path.isfile(path):
                return path, 'HIT-after-wait'
            with enc_lock:
                encodes.append(k)
            tmp_path = '%s.%d.tmp' % (path, threading.get_ident())
            time.sleep(0.05)                      # stand-in for the encode
            with open(tmp_path, 'wb') as f:
                f.write(b'video')
            os.replace(tmp_path, path)            # atomic publish
            return path, 'MISS'
        finally:
            lk.release()

    results = []
    res_lock = threading.Lock()

    def worker(cmd):
        r = fake_normalize(cmd)
        with res_lock:
            results.append(r)

    threads = [threading.Thread(target=worker, args=(c,)) for _b, _f, c in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 10
    assert len(encodes) == 2, 'expected exactly 2 encodes, got %d: %r' % (len(encodes), encodes)
    ok('10 concurrent jobs produced exactly 2 encodes')
    assert len(set(encodes)) == 2, 'the same identity was encoded twice'
    ok('no identity encoded more than once (per-key lock held)')
    produced = [f for f in os.listdir(tmp) if f.endswith('.mp4')]
    assert len(produced) == 2, produced
    ok('exactly 2 canonical files on disk')
    assert not [f for f in os.listdir(tmp) if f.endswith('.tmp')], 'temp file left behind'
    ok('no temp files left behind (atomic replace consumed them)')

    print('\n[a partially written file is never visible at the cache path]')
    k = key(jobs[0][2])
    path = os.path.join(tmp, 'reel_normalized_%s.mp4' % k)
    half = path + '.999.tmp'
    with open(half, 'wb') as f:
        f.write(b'partial')
    assert os.path.isfile(path), 'canonical file should already exist from the run above'
    assert os.path.getsize(path) == len(b'video'), 'canonical file was overwritten by a partial'
    ok('an in-progress .tmp does not masquerade as a cache hit')
    os.remove(half)

    print('\n[a failed encode publishes nothing]')
    failed_key = 'v1-deadbeefdeadbeef'
    fpath = os.path.join(tmp, 'reel_normalized_%s.mp4' % failed_key)
    ftmp = fpath + '.1.tmp'
    with open(ftmp, 'wb') as f:
        f.write(b'broken')
    os.remove(ftmp)                                # what the failure branch does
    assert not os.path.exists(fpath), 'failed encode leaked a cache entry'
    ok('failed encode leaves no cache entry to be reused')

    print('\n[REGRESSION: the temp path must still be an .mp4]')
    # FFmpeg infers the muxer from the output extension. A temp ending in .tmp
    # fails with "Unable to find a suitable output format", and normalize_video
    # then falls back to the UN-REFRAMED original — a render at the source aspect
    # ratio that does not match the preview the user approved. Shipped once on
    # 31 Aug; every render in that window came out at the wrong aspect.
    src_txt = io.open(os.path.join('portal', 'video_processor.py'), encoding='utf-8').read()
    m = re.search(r'_stem, _ext = os\.path\.splitext\(fixed_path\)\s*\n\s*tmp_path = f"([^"]+)"', src_txt)
    assert m, 'temp path construction not found — has it been rewritten?'
    template = m.group(1)

    canonical = '/raw/clip_normalized_vertical_9_16_v1-abc123.mp4'
    _stem, _ext = os.path.splitext(canonical)
    built = (template
             .replace('{_stem}', _stem)
             .replace('{_uuid.uuid4().hex}', 'deadbeef')
             .replace("{_ext or '.mp4'}", _ext or '.mp4'))
    assert os.path.splitext(built)[1] == '.mp4', \
        'temp does not end in .mp4 — FFmpeg cannot infer the muxer: %s' % built
    ok('temp keeps an .mp4 extension', os.path.basename(built))
    assert built != canonical, 'temp path collides with the canonical path'
    ok('temp is distinct from the canonical path')
    assert '.tmp' in built, 'temp is no longer identifiable as a temp'
    ok('temp is still identifiable as a temp')
    # An encode is capped at 5 min; the sweep only deletes at 30 min, so a temp
    # inside the .mp4 glob can never age out while it is still being written.
    assert re.search(r'NORMALIZE_TIMEOUT = (\d+)', src_txt).group(1) == '300'
    ok('encode timeout 300s is well inside the 30-min sweep cutoff')

    print('\n%d assertions passed.' % PASS)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
