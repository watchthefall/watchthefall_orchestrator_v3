"""Seam test: a saved reframe must survive the resolver and reach the renderer.

WHY THIS FILE EXISTS. simulate_reframe.py opens video_processor.py and
clean_dashboard.html -- and never opens app.py. It proved the UI offers 1:1
reframe, and proved the renderer honours 1:1 reframe, and never proved anything
connected them. It didn't: _resolve_render_source_edit discarded every
non-vertical crop one call before the renderer saw it. Both ends asserted, the
middle unexamined, the feature inert.

So this suite tests the middle. It does NOT re-implement the resolver -- it
AST-extracts the real function out of portal/app.py and executes it against
stub persistence, so the thing under test is the shipped code. It then asserts
a cross-file invariant: every format allowed to SAVE a reframe must have a
renderer branch that APPLIES one.

No Flask, no DB, no FFmpeg, no network, no credits. Run from the repo root:
    python scripts/simulate_source_edit_resolver.py
"""
import ast
import contextlib
import io
import os

APP = io.open(os.path.join('portal', 'app.py'), encoding='utf-8').read()
DB = io.open(os.path.join('portal', 'database.py'), encoding='utf-8').read()
VP = io.open(os.path.join('portal', 'video_processor.py'), encoding='utf-8').read()

PASS = 0


def ok(label, extra=''):
    global PASS
    PASS += 1
    print('  ok  %-54s %s' % (label, extra))


def extract(src, *names):
    """Source text of the named top-level defs/assignments, in file order."""
    wanted, out = set(names), []
    for node in ast.parse(src).body:
        nm = None
        if isinstance(node, ast.FunctionDef):
            nm = node.name
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            nm = node.targets[0].id
        if nm in wanted:
            out.append((nm, ast.get_source_segment(src, node)))
    missing = wanted - {n for n, _ in out}
    assert not missing, 'could not extract from source: %s' % sorted(missing)
    return out


# --- build a namespace holding the REAL code under test --------------------
PERSISTED = {}


def get_source_edit(user_id, source_filename, output_format):
    return PERSISTED.get((user_id, source_filename, output_format))


ns = {'get_source_edit': get_source_edit}
for _nm, _seg in extract(DB, 'SOURCE_EDIT_DEFAULTS'):
    exec(_seg, ns)
for _nm, _seg in extract(APP, 'SOURCE_EDIT_FORMATS', 'SOURCE_EDIT_CROP_MODES',
                         '_clamp', '_resolve_render_source_edit',
                         '_is_default_source_edit'):
    exec(_seg, ns)

FORMATS = ns['SOURCE_EDIT_FORMATS']
_resolve = ns['_resolve_render_source_edit']
_is_default = ns['_is_default_source_edit']


def resolve(fmt, payload, user=1, src='clip.mp4'):
    """Call the real resolver with its chatter suppressed."""
    with contextlib.redirect_stdout(io.StringIO()):
        return _resolve(user, src, fmt, payload)


def legacy_resolve(fmt, payload):
    """The gate as it shipped, for an explicit before/after contrast."""
    edit = payload if isinstance(payload, dict) else ns['SOURCE_EDIT_DEFAULTS'].copy()
    flip_h = 1 if edit.get('flip_h') else 0
    if fmt != 'vertical_9_16':
        return {'flip_h': flip_h} if flip_h else None
    return resolve(fmt, payload)


EDIT = {'crop_x': 0.2, 'crop_y': 0.8, 'zoom': 1.5, 'crop_mode': 'fill'}

print('\n[the bug, stated as a difference]')
before = legacy_resolve('square_1_1', dict(EDIT))
after = resolve('square_1_1', dict(EDIT))
assert before is None, before
ok('BEFORE: a 1:1 reframe resolved to', repr(before))
assert isinstance(after, dict) and after['crop_x'] == 0.2, after
ok('AFTER:  the same 1:1 reframe resolves to', 'crop_x=%.2f crop_y=%.2f zoom=%.2f'
   % (after['crop_x'], after['crop_y'], after['zoom']))

print('\n[every reframe-capable format carries its edit through]')
for fmt in sorted(FORMATS):
    r = resolve(fmt, dict(EDIT))
    assert isinstance(r, dict), '%s resolved to %r' % (fmt, r)
    assert r['crop_x'] == 0.2 and r['crop_y'] == 0.8 and r['zoom'] == 1.5, (fmt, r)
    assert r['crop_mode'] == 'fill', (fmt, r)
    ok('%s survives the resolver' % fmt, 'crop/zoom/mode intact')

print('\n[the real journey: saved in the UI, rendered later with no payload]')
PERSISTED[(1, 'clip.mp4', 'square_1_1')] = {'crop_x': 0.15, 'crop_y': 0.35,
                                            'zoom': 2.0, 'crop_mode': 'fit',
                                            'flip_h': 0}
r = resolve('square_1_1', None)
assert r and r['crop_x'] == 0.15 and r['zoom'] == 2.0, r
ok('persisted 1:1 edit is loaded and carried', 'crop_x=0.15 zoom=2.0')
assert legacy_resolve('square_1_1', None) is None
ok('...and was previously dropped entirely', 'the user-visible defect')
PERSISTED.clear()

print('\n[formats with no reframe branch are still gated]')
# Chosen dynamically: hardcoding landscape_16_9 here would turn this section
# into a false failure the day 16:9 is legitimately added, masking the
# cross-file invariant below (which is the assertion that actually matters).
UNSUPPORTED = next(f for f in ('landscape_16_9', 'auto_pack', '__no_such_format__')
                   if f not in FORMATS)
r = resolve(UNSUPPORTED, dict(EDIT))
assert r is None, r
ok('unsupported format drops crop', '%s -> None' % UNSUPPORTED)
r = resolve(UNSUPPORTED, dict(EDIT, flip_h=1))
assert r == {'flip_h': 1}, r
ok('unsupported format still honours flip', repr(r))

print('\n[clamping and validation still apply on the newly-open path]')
r = resolve('square_1_1', {'crop_x': 5.0, 'crop_y': -3.0, 'zoom': 99.0,
                           'crop_mode': 'fill'})
assert r['crop_x'] == 1.0 and r['crop_y'] == 0.0 and r['zoom'] == 4.0, r
ok('out-of-range 1:1 values clamp', 'crop_x=1.0 crop_y=0.0 zoom=4.0')
r = resolve('square_1_1', {'crop_mode': 'nonsense'})
assert r['crop_mode'] == 'fit', r
ok('invalid crop_mode falls back to fit')
r = resolve('square_1_1', {'crop_x': 'abc', 'zoom': None})
assert r['crop_x'] == 0.5 and r['zoom'] == 1.0, r
ok('junk values fall back to defaults')

print('\n[a default 1:1 edit still skips the pipeline (no needless re-encode)]')
assert _is_default({'crop_x': .5, 'crop_y': .5, 'zoom': 1.0, 'crop_mode': 'fill'})
ok('centred fill is still treated as default')
assert not _is_default({'crop_x': .5, 'crop_y': .5, 'zoom': 1.0,
                        'crop_mode': 'fill', 'flip_h': 1})
ok('a flip is never treated as default')

print('\n[THE INVARIANT: save-side and render-side agree, by construction]')
assert 'output_format not in SOURCE_EDIT_FORMATS' in APP
ok('resolver gates on the shared set')
assert "!= 'vertical_9_16':\n        # Non-vertical" not in APP
ok('the hardcoded vertical-only gate is gone')

# Every format allowed to SAVE a reframe must have a renderer branch that
# APPLIES one. This is the assertion whose absence let the bug ship, and it is
# the guardrail for landscape_16_9: adding it to SOURCE_EDIT_FORMATS without a
# normalize_video branch fails here rather than silently rendering unreframed.
norm = next(n for n in ast.parse(VP).body
            if isinstance(n, ast.FunctionDef) and n.name == 'normalize_video')
applies = {}
for node in ast.walk(norm):
    if not isinstance(node, ast.If):
        continue
    for cmp_node in ast.walk(node.test):
        if not (isinstance(cmp_node, ast.Compare)
                and isinstance(cmp_node.left, ast.Name)
                and cmp_node.left.id == 'output_format'):
            continue
        for c in cmp_node.comparators:
            if isinstance(c, ast.Constant) and isinstance(c.value, str):
                body = '\n'.join(ast.get_source_segment(VP, s) or ''
                                 for s in node.body)
                applies[c.value] = (applies.get(c.value, False)
                                    or ('_build_reframe_filter' in body))

for fmt in sorted(FORMATS):
    assert applies.get(fmt), (
        '%s may save a reframe but normalize_video has no branch applying one '
        '(branches found: %r)' % (fmt, applies))
    ok('%s: renderer branch applies the reframe' % fmt)

print('\n%d assertions passed.' % PASS)
