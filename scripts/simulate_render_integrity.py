"""Offline assertions that a broken render cannot look like a successful one.

Two defences, from the 31 Aug incident where a vertical_9_16 request produced a
1280x720 landscape file, was recorded in the database as 720x1280, and charged
the user a credit:

  1. normalization failure must FAIL the render (never fall back to the
     un-reframed source), so charge-on-success is never reached;
  2. the recorded dimensions must be MEASURED from the finished file and
     validated against the format contract, not copied from the request.

Source-level checks: these guard the control flow, which is what actually failed.
No Flask, no DB, no FFmpeg, no network, no credits. Run from the repo root:
    python scripts/simulate_render_integrity.py
"""
import io
import os
import re

VP = io.open(os.path.join('portal', 'video_processor.py'), encoding='utf-8').read()
APP = io.open(os.path.join('portal', 'app.py'), encoding='utf-8').read()

PASS = 0


def ok(label):
    global PASS
    PASS += 1
    print('  ok  %s' % label)


print('\n[1. normalization failure is fatal]')
assert 'class NormalizationError' in VP
ok('NormalizationError exists')

# The exact defect: normalize_video used to hand back the ORIGINAL file on every
# failure path, so the caller could not tell success from silent degradation.
body = VP[VP.index('def normalize_video('):]
body = body[:body.index('\ndef ')] if '\ndef ' in body[10:] else body
assert 'return input_path' not in body, \
    'normalize_video still falls back to the un-reframed original on failure'
ok('no failure path returns the original input')

for label, needle in [
    ('non-zero exit raises',   'raise NormalizationError('),
    ('timeout raises',         'normalization timed out after'),
    ('unexpected error raises', 'normalization error for'),
]:
    assert needle in body, '%s — missing %r' % (label, needle)
    ok(label)

# A successful normalize must still return the produced path, not raise.
assert 'return fixed_path' in body
ok('success still returns the normalized path')

print('\n[2. dimensions are measured, not assumed]')
assert 'def probe_dimensions' in VP
ok('probe_dimensions exists')
assert "EXPECTED_OUTPUT_DIMS = {" in VP
for fmt, dims in [("'vertical_9_16': (720, 1280)", '9:16'), ("'square_1_1':    (720, 720)", '1:1')]:
    assert fmt in VP, fmt
    ok('format contract declared for %s' % dims)

# The old code assigned dimensions from the REQUEST. That is the line that let a
# 1280x720 file be recorded as 720x1280.
save_block = APP[APP.index('# Measure what was ACTUALLY produced'):]
save_block = save_block[:save_block.index('except Exception as _bo_e')]
assert '_bw, _bh = probe_dimensions(output_path)' in save_block
ok('width/height come from probing the finished file')
assert re.search(r"_bw, _bh, _bar = 720, 1280", APP) is None, \
    'hardcoded 720x1280 assignment still present — dimensions would be assumed again'
ok('no hardcoded dimension assignment remains on the save path')

print('\n[3. a wrong-sized output cannot be recorded or charged]')
assert 'refusing to record it' in save_block
ok('measured mismatch raises rather than recording')
# The raise must sit BEFORE save_branded_output, or a bad file still gets a row.
assert save_block.index('refusing to record it') < save_block.index('save_branded_output('), \
    'mismatch is raised after the row is written'
ok('mismatch is raised before the row is written')
# ...and before the credit charge, which lives after the brand loop.
assert APP.index('refusing to record it') < APP.index('spend_credits(user_id, 1, _allowance)'), \
    'mismatch is raised after the credit is charged'
ok('mismatch is raised before charge-on-success')

print('\n[4. a probe failure must NOT fail a good render]')
assert 'recording without them' in save_block
ok('unmeasurable dimensions are recorded as absent, not fatal')
# Guard the distinction explicitly: the raise is conditional on having MEASURED
# values. If it fired on None it would kill valid renders whenever ffprobe blipped.
cond = re.search(r'if _bw and _bh and _expected and \(_bw, _bh\) != _expected:', save_block)
assert cond, 'mismatch check does not require measured values first'
ok('mismatch check only fires when dimensions were actually measured')

print('\n[5. the credit model itself is untouched]')
assert APP.count('spend_credits(user_id, 1, _allowance)') == 1
ok('still exactly one charge site')
for absent in ('refund_credit', 'restore_credits', 'reverse_credit'):
    assert absent not in APP, 'credit accounting was changed: %s' % absent
ok('no refund/reversal logic was introduced')

print('\n%d assertions passed.' % PASS)
