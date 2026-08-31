"""Offline assertions for normalized-file lifetime protection (commit 1).

Covers the reference registry and its integration with the age-based sweep,
including the two races that make sharing unsafe without it:

  * job A creates a normalized file -> job B references it -> sweep runs
    -> job B's input must survive
  * cache lookup -> file disappears -> claim detects absence -> caller
    regenerates rather than handing a dead path to FFmpeg

No Flask, no DB, no FFmpeg, no network, no credits. Run from the repo root:
    python scripts/simulate_normalized_protection.py
"""
import os
import sys
import glob
import time
import shutil
import tempfile
import threading

# Load the module directly by path: importing `portal.normalized_cache` would
# execute portal/__init__.py, which imports Flask. The module itself depends on
# nothing but the stdlib, which is what makes this harness possible at all.
import importlib.util                                            # noqa: E402
_spec = importlib.util.spec_from_file_location(
    'normalized_cache', os.path.join('portal', 'normalized_cache.py'))
nc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nc)

PASS = 0


def ok(label):
    global PASS
    PASS += 1
    print('  ok  %s' % label)


def sweep(raw_dir, max_age_minutes):
    """Mirror of database.sweep_normalized_temp_files, minus the Flask/config import."""
    cutoff = time.time() - max_age_minutes * 60
    protected = nc.protected_paths()
    deleted, skipped = 0, 0
    for path in glob.glob(os.path.join(raw_dir, '*_normalized_*.mp4')):
        if os.path.abspath(path) in protected:
            skipped += 1
            continue
        if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
            os.remove(path)
            deleted += 1
    return deleted, skipped


tmp = tempfile.mkdtemp(prefix='brandr_norm_')
try:
    def make(name, age_minutes=0):
        p = os.path.join(tmp, name)
        with open(p, 'wb') as f:
            f.write(b'x' * 16)
        if age_minutes:
            old = time.time() - age_minutes * 60
            os.utime(p, (old, old))
        return p

    print('\n[reference counting]')
    a = make('vidA_normalized_vertical_9_16_aaa.mp4')
    assert nc.claim(a) is True
    ok('claim on an existing file succeeds')
    assert nc.is_protected(a)
    ok('claimed path reports protected')
    nc.claim(a)                      # second holder
    nc.release(a)
    assert nc.is_protected(a), 'released too early — two holders, one release'
    ok('two claims need two releases')
    nc.release(a)
    assert not nc.is_protected(a)
    ok('final release clears protection')
    assert nc.active_count() == 0
    ok('registry empties (no leak)')

    print('\n[claim detects a file that has gone]')
    ghost = os.path.join(tmp, 'vidG_normalized_vertical_9_16_ggg.mp4')
    assert nc.claim(ghost) is False
    ok('claim on a missing file returns False')
    assert not nc.is_protected(ghost), 'a missing file must not stay pinned'
    ok('failed claim leaves no phantom reference')
    assert nc.active_count() == 0
    ok('registry still empty after a failed claim')

    print('\n[the race that motivates all of this]')
    # Job A produced it 45 minutes ago; job B is still queued behind a long batch.
    shared = make('vidS_normalized_vertical_9_16_shared.mp4', age_minutes=45)
    deleted, skipped = sweep(tmp, 30)
    assert deleted == 1 and not os.path.exists(shared)
    ok('UNREFERENCED stale file is swept (existing behaviour preserved)')

    shared = make('vidS_normalized_vertical_9_16_shared.mp4', age_minutes=45)
    assert nc.claim(shared) is True          # job B claims it
    deleted, skipped = sweep(tmp, 30)        # sweep fires mid-batch
    assert skipped == 1, 'sweep did not skip the referenced file'
    assert deleted == 0
    assert os.path.exists(shared), 'referenced input was deleted mid-render'
    ok('REFERENCED stale file survives the sweep — job B keeps its input')
    nc.release(shared)
    # claim() refreshed the mtime, so the file is legitimately young again and the
    # sweep is right to leave it. Age it back to prove that a released file with no
    # remaining references is once more eligible.
    _old = time.time() - 45 * 60
    os.utime(shared, (_old, _old))
    deleted, _ = sweep(tmp, 30)
    assert deleted == 1, 'released + stale file should be swept'
    ok('once released AND stale again, the same file is swept normally')

    print('\n[claim refreshes mtime so continued use stays clear of the cutoff]')
    warm = make('vidW_normalized_vertical_9_16_warm.mp4', age_minutes=45)
    assert nc.claim(warm) is True
    nc.release(warm)
    deleted, _ = sweep(tmp, 30)
    assert deleted == 0 and os.path.exists(warm), 'mtime was not refreshed on claim'
    ok('claiming a stale file makes it fresh again')
    os.remove(warm)

    print('\n[references cannot leak when a render raises]')
    err = make('vidE_normalized_vertical_9_16_err.mp4')
    claimed = None
    try:
        assert nc.claim(err)
        claimed = err
        raise RuntimeError('render blew up')
    except RuntimeError:
        pass
    finally:
        if claimed:
            nc.release(claimed)
    assert nc.active_count() == 0
    ok('finally-release survives an exception mid-render')

    print('\n[concurrent claims are counted correctly]')
    conc = make('vidC_normalized_vertical_9_16_conc.mp4')
    errors = []

    def worker():
        try:
            for _ in range(200):
                if nc.claim(conc):
                    nc.release(conc)
        except Exception as e:          # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert nc.active_count() == 0, 'refcount drifted under concurrency: %r' % nc.debug_snapshot()
    ok('8 threads x 200 claim/release cycles leave the registry empty')

    print('\n%d assertions passed.' % PASS)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
