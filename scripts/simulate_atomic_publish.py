"""The delivered artifact must never be a blend of two concurrent renders.

THE DEFECT. process_brand built a fully deterministic output name --
{video_id}_{brand}_{format}.mp4 -- and handed that path straight to FFmpeg.
Two renders of the same (video, brand, format) therefore opened the SAME file
with -y and interleaved their writes. _validate_output then probed a file the
other job was still writing, so a render could be certified, written to
branded_outputs and charged a credit while its bytes were still changing.

That is the 31 Aug failure class again: an internal assumption -- "the file I
validated is the file that persists" -- masquerading as success. normalize_video
has published atomically since that incident; the brand render never did.

Deterministic DELIVERED filenames are correct and are kept. What changed is the
WORKING path: each render writes its own file and publishes with os.replace, so
concurrent jobs produce a last-writer-wins COMPLETE file instead of a mixture.

Part 1 asserts the shipped code has that shape. Part 2 demonstrates the two
regimes against the real filesystem, with no FFmpeg and no network.

    python scripts/simulate_atomic_publish.py
"""
import io
import os
import re
import shutil
import tempfile
import time
import threading

SRC = io.open(os.path.join('portal', 'video_processor.py'), encoding='utf-8').read()

PASS = 0


def ok(label, extra=''):
    global PASS
    PASS += 1
    print('  ok  %-54s %s' % (label, extra))


# ---------------------------------------------------------------- part 1 ----
print('\n[the shipped code renders to a work path, not the delivered one]')

body = SRC[SRC.index('def process_brand('):SRC.index('def process_multiple_brands(')]

assert 'work_path = f"{_out_stem}.{_uuid.uuid4().hex}.tmp{_out_ext or \'.mp4\'}"' in body
ok('work path is unique per render', 'uuid4 hex')

# The 31 Aug bug in one assertion: a temp name ending '.tmp' breaks FFmpeg's
# extension-based muxer inference, which silently produced a landscape file for
# a vertical request. The extension must survive into the temp name.
assert '_out_stem, _out_ext = os.path.splitext(output_path)' in body
assert ".tmp{_out_ext or '.mp4'}" in body
ok('work path KEEPS the .mp4 extension', 'muxer inference regression')

assert "tail_cmd = ['-movflags', '+faststart', work_path]" in body
ok('FFmpeg writes to the work path')
assert "'+faststart', output_path]" not in body
ok('FFmpeg is never handed the delivered path')

assert 'self._validate_output(work_path)' in body
ok('validation probes the file THIS job wrote')
assert 'self._validate_output(output_path)' not in body
ok('validation never probes the shared destination')

assert 'os.replace(work_path, output_path)' in body
ok('publish is atomic', 'os.replace')

# Publish must happen only after the file probes valid, or an invalid encode
# would replace a good delivered artifact.
pub = body.index('os.replace(work_path, output_path)')
val = body.index('output_valid = self._validate_output(work_path)')
gate = body.index('if output_valid:')
assert val < gate < pub, (val, gate, pub)
ok('publish happens only inside the valid branch')

# EVERY exit that doesn't publish must clean up. These files live in OUTPUT_DIR
# under a name sweep_normalized_temp_files never globs (it only matches
# '*_normalized_*' inside RAW_DIR), so anything stranded here is stranded for
# good on a 5 GB disk.
assert body.count('_discard_work_file(work_path)') == 3
ok('all three non-publishing exits clean up',
   'timeout + attempts exhausted + failed publish')
assert 'def _discard_work_file' in SRC and 'except OSError' in SRC
ok('cleanup helper never raises from a failure path')

# The publish itself is the third exit: a failure there would otherwise leave a
# fully-rendered file with no owner and no sweeper.
pub_guard = body[body.index('try:\n                    os.replace'):]
pub_guard = pub_guard[:pub_guard.index('print(f"[RENDER] Published')]
assert 'except OSError' in pub_guard and '_discard_work_file(work_path)' in pub_guard
ok('a failed publish discards rather than strands')


# ---------------------------------------------------------------- part 2 ----
# Same filesystem, same threads, two regimes. Content stands in for an encode:
# what matters is whether a reader can observe a mixture.
print('\n[against the real filesystem: what each regime actually produces]')

CHUNK = b'A' * 4096
CHUNKB = b'B' * 4096
REPS = 400


def direct_writer(path, chunk, barrier):
    barrier.wait()
    with open(path, 'wb') as f:
        for _ in range(REPS):
            f.write(chunk)
            f.flush()


def atomic_writer(path, chunk, barrier):
    stem, ext = os.path.splitext(path)
    tmp = '%s.%s.tmp%s' % (stem, threading.current_thread().name, ext)
    barrier.wait()
    with open(tmp, 'wb') as f:
        for _ in range(REPS):
            f.write(chunk)
            f.flush()
    # Windows can transiently refuse a replace while another replace targeting
    # the same destination is in flight; POSIX rename(2) cannot. Retry, then
    # discard rather than strand -- mirroring what process_brand now does, and
    # the reason it now does it: an unguarded replace here stranded a file on
    # the first run of this suite.
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except OSError:
            if attempt == 4:
                break
            time.sleep(0.05)
    try:
        os.remove(tmp)
    except OSError:
        pass


def run(writer, workdir):
    target = os.path.join(workdir, 'clip_aiwtf_landscape_16_9.mp4')
    barrier = threading.Barrier(2)
    ts = [threading.Thread(target=writer, args=(target, c, barrier), name='w%d' % i)
          for i, c in enumerate((CHUNK, CHUNKB))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    data = open(target, 'rb').read()
    return {
        'bytes': len(data),
        'has_A': b'A' in data,
        'has_B': b'B' in data,
        'leftovers': [f for f in os.listdir(workdir) if '.tmp' in f],
    }


work = tempfile.mkdtemp(prefix='brandr_pub_')
try:
    unsafe_dir = os.path.join(work, 'unsafe')
    safe_dir = os.path.join(work, 'safe')
    os.makedirs(unsafe_dir)
    os.makedirs(safe_dir)

    unsafe = run(direct_writer, unsafe_dir)
    safe = run(atomic_writer, safe_dir)

    expected = len(CHUNK) * REPS
    mixed_unsafe = unsafe['has_A'] and unsafe['has_B']

    print('  OLD (both write the delivered path directly)')
    print('      size=%d (one clean encode would be %d)' % (unsafe['bytes'], expected))
    print('      contains BOTH renders: %s' % mixed_unsafe)
    print('  NEW (each writes its own file, publishes with os.replace)')
    print('      size=%d' % safe['bytes'])
    print('      contains BOTH renders: %s' % (safe['has_A'] and safe['has_B']))
    print('      stranded temp files: %d' % len(safe['leftovers']))

    # The guarantee. Not "the old way always corrupts" -- interleaving is a race
    # and a race is allowed not to fire -- but "the new way never can".
    assert not (safe['has_A'] and safe['has_B']), 'atomic publish produced a mixture'
    ok('atomic publish yields exactly ONE render', 'never a blend')
    assert safe['bytes'] == expected, safe['bytes']
    ok('published file is a COMPLETE encode', '%d bytes' % safe['bytes'])
    assert not safe['leftovers'], safe['leftovers']
    ok('no temp files stranded after publish')

    if mixed_unsafe:
        ok('reproduced the defect on the old path', 'file held BOTH renders')
    else:
        print('      (the old path did not interleave this run -- it is a race, '
              'not a certainty; the guarantee above is what matters)')
finally:
    shutil.rmtree(work, ignore_errors=True)

print('\n%d assertions passed.' % PASS)
