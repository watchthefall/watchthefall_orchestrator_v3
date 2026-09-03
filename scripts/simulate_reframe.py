"""Offline assertions for Reframe v1 (Fit / Fill / Manual).

Reframe v1 is deliberately NOT a new rendering capability — crop_x/crop_y are
already continuous 0-1 values that the filter consumes, so "Manual" is exposing
control the pipeline already had. These tests pin the behaviour that must hold:

  * Fit on a landscape source blur-pads instead of showing black bars
  * Fill (source already covers the canvas) does NOT pay for a blur pass
  * a flipped source gets a flipped backdrop, not a mirror sitting on a ghost
  * crop_x/crop_y move the frame continuously and stay inside the canvas

No Flask, no DB, no FFmpeg, no network, no credits. Run from the repo root:
    python scripts/simulate_reframe.py
"""
import io
import os
import re

SRC = io.open(os.path.join('portal', 'video_processor.py'), encoding='utf-8').read()

PASS = 0


def ok(label, extra=''):
    global PASS
    PASS += 1
    print('  ok  %-52s %s' % (label, extra))


def _even(v):
    return max(2, int(round(v / 2.0) * 2))


def reframe(vw, vh, mode='fit', crop_x=0.5, crop_y=0.5, zoom=1.0, flip=0,
            target_w=720, target_h=1280):
    """Mirror of _build_vertical_reframe_filter's geometry + background choice."""
    base = max(target_w / vw, target_h / vh) if mode == 'fill' else min(target_w / vw, target_h / vh)
    scale = base * zoom
    sw, sh = _even(vw * scale), _even(vh * scale)
    ox = int(round((target_w - sw) * crop_x))
    oy = int(round((target_h - sh) * crop_y))
    covers = sw >= target_w and sh >= target_h
    return dict(sw=sw, sh=sh, ox=ox, oy=oy, bg='none' if covers else 'blur-pad')


print('\n[the change: 9:16 Fit no longer shows a black void]')
land = reframe(1280, 720, 'fit')
assert land['bg'] == 'blur-pad', land
ok('landscape 1280x720 -> Fit', 'scaled %dx%d, bg=%s' % (land['sw'], land['sh'], land['bg']))
assert land['sh'] < 1280, 'a landscape Fit should leave vertical space to fill'
ok('bars really would have been visible', '%dpx of canvas unfilled' % (1280 - land['sh']))

print('\n[Fill must not pay for an invisible blur]')
fill = reframe(1280, 720, 'fill')
assert fill['bg'] == 'none', fill
ok('landscape -> Fill skips the blur pass', 'scaled %dx%d covers canvas' % (fill['sw'], fill['sh']))
assert fill['sw'] >= 720 and fill['sh'] >= 1280
ok('Fill genuinely covers the canvas')
# ...and this is what Fill costs, which is why Fit needed fixing rather than
# Fill becoming the default.
kept = 720 / fill['sw']
print('      Fill keeps %.0f%% of the source width (discards %.0f%%)'
      % (kept * 100, (1 - kept) * 100))
assert kept < 0.4, 'expected Fill to crop heavily on a 16:9 source'
ok('Fill discards most of a landscape frame', 'why Fit had to improve')

print('\n[a source that already fits needs no background either]')
vert = reframe(720, 1280, 'fit')
assert vert['bg'] == 'none', vert
ok('9:16 source -> Fit needs no blur', 'scaled %dx%d' % (vert['sw'], vert['sh']))

print('\n[zoomed-out Fit exposes canvas, so it blur-pads]')
zoomed = reframe(720, 1280, 'fit', zoom=0.64)
assert zoomed['bg'] == 'blur-pad', zoomed
ok('zoom 0.64 on a 9:16 source blur-pads', 'scaled %dx%d' % (zoomed['sw'], zoomed['sh']))

print('\n[Manual: crop_x/crop_y move the frame continuously]')
left = reframe(720, 1280, 'fill', crop_x=0.0, zoom=1.5)
mid = reframe(720, 1280, 'fill', crop_x=0.5, zoom=1.5)
right = reframe(720, 1280, 'fill', crop_x=1.0, zoom=1.5)
assert left['ox'] > mid['ox'] > right['ox'], (left['ox'], mid['ox'], right['ox'])
ok('crop_x pans horizontally', '%d -> %d -> %d' % (left['ox'], mid['ox'], right['ox']))
top = reframe(1280, 720, 'fit', crop_y=0.0)
bot = reframe(1280, 720, 'fit', crop_y=1.0)
assert top['oy'] == 0 and bot['oy'] == 1280 - top['sh']
ok('crop_y pans vertically', 'top oy=%d, bottom oy=%d' % (top['oy'], bot['oy']))
# Continuous, not the three-step Top/Centre/Bottom the UI exposes today.
seen = {reframe(1280, 720, 'fit', crop_y=c)['oy'] for c in (0, .17, .33, .5, .67, .83, 1)}
assert len(seen) == 7, seen
ok('7 distinct positions from 7 crop_y values', 'model is continuous already')

print('\n[the frame can never leave the canvas]')
for cx in (0.0, 0.25, 0.5, 0.75, 1.0):
    for cy in (0.0, 0.5, 1.0):
        for mode in ('fit', 'fill'):
            r = reframe(1280, 720, mode, crop_x=cx, crop_y=cy)
            if r['sw'] <= 720:
                assert 0 <= r['ox'] <= 720 - r['sw'], r
            else:
                assert 720 - r['sw'] <= r['ox'] <= 0, r
ok('offsets stay in range across 30 mode/position combinations')

print('\n[the filter string itself]')
assert 'gblur=sigma=25' in SRC
ok('blur-pad uses the same sigma as the proven 1:1 path')
m = re.search(r'covers_canvas = ([^\n]+)', SRC)
assert m and 'sw >= target_w and sh >= target_h' in m.group(1)
ok('blur is conditional on the canvas actually being exposed')
# Flip must precede split, or the backdrop mirrors independently of the video.
vert_filter = SRC[SRC.index('split=2[fg][bg_raw]') - 200:SRC.index('split=2[fg][bg_raw]') + 40]
assert '{flip_pre}split=2' in vert_filter, vert_filter[-80:]
ok('flip is applied before split', 'backdrop mirrors with the foreground')


print('\n[1:1 now honours reframe — it previously ignored it entirely]')
VP = SRC
sq = VP[VP.index("elif output_format == 'square_1_1'"):]
# Anchor to line start at exactly 8 spaces: the branch now contains an inner
# 12-space `else:`, and an unanchored search matches that first, silently
# truncating the slice before the blur-pad filter it is meant to check.
sq = sq[:sq.index(chr(10) + ' ' * 8 + 'else:')]
assert '_build_reframe_filter(' in sq, 'square path still ignores crop_x/crop_y/zoom'
ok('square path calls the reframe filter')
assert '720, 720' in sq, 'square passes the wrong target size'
ok('square passes a 720x720 target')

print('\n[REGRESSION: an untouched 1:1 job must be byte-identical]')
assert '_is_default_reframe(source_edit)' in sq, 'no default guard — every square job would re-encode'
ok('default edits keep the original filter')
# The original blur-pad string must survive verbatim, or cached square entries
# are invalidated and already-correct work is re-encoded for a ~1px difference.
for frag in ('force_original_aspect_ratio=increase',
             'crop=720:720:(iw-720)/2:(ih-720)/2',
             'gblur=sigma=25',
             'force_original_aspect_ratio=decrease',
             'overlay=(W-w)/2:(H-h)/2[out]'):
    assert frag in sq, 'original blur-pad altered: %s' % frag
ok('original blur-pad filter preserved verbatim', '5 fragments intact')
assert sq.count('cmd = [') == 1, 'more than one cmd assignment — the branch can overwrite itself'
ok('exactly one cmd assignment in the square branch')

print('\n[the default guard is strict about what "untouched" means]')
def is_default(e):
    if not isinstance(e, dict):
        return True
    try:
        return (abs(float(e.get('crop_x', .5)) - .5) < 1e-9
                and abs(float(e.get('crop_y', .5)) - .5) < 1e-9
                and abs(float(e.get('zoom', 1.)) - 1.) < 1e-9
                and str(e.get('crop_mode', 'fit')) == 'fit')
    except (TypeError, ValueError):
        return False
assert is_default(None) and is_default({}) 
ok('no edit / empty edit counts as default')
assert is_default({'crop_x': .5, 'crop_y': .5, 'zoom': 1.0, 'crop_mode': 'fit'})
ok('explicit defaults count as default')
for changed in ({'crop_x': .3}, {'crop_y': .8}, {'zoom': 1.5}, {'crop_mode': 'fill'}):
    e = {'crop_x': .5, 'crop_y': .5, 'zoom': 1.0, 'crop_mode': 'fit'}
    e.update(changed)
    assert not is_default(e), 'change ignored: %r' % changed
    ok('%s makes it non-default' % list(changed)[0])

print('\n[square geometry through the generalised filter]')
sq_fit = reframe(1920, 1080, 'fit', target_w=720, target_h=720)
assert sq_fit['bg'] == 'blur-pad', sq_fit
ok('landscape -> 1:1 Fit blur-pads', 'scaled %dx%d' % (sq_fit['sw'], sq_fit['sh']))
sq_l = reframe(1920, 1080, 'fill', crop_x=0.0, target_w=720, target_h=720)
sq_r = reframe(1920, 1080, 'fill', crop_x=1.0, target_w=720, target_h=720)
assert sq_l['ox'] > sq_r['ox'], (sq_l['ox'], sq_r['ox'])
ok('1:1 crop_x pans horizontally', '%d -> %d' % (sq_l['ox'], sq_r['ox']))

print('\n[frontend gating is a named predicate, not scattered comparisons]')
UI = io.open(os.path.join('portal', 'templates', 'clean_dashboard.html'), encoding='utf-8').read()
assert "REFRAMABLE_FORMATS = new Set(['vertical_9_16', 'square_1_1'])" in UI
ok('both live formats are reframable in the UI')
assert "activeReviewFormat !== 'vertical_9_16'" not in UI, 'a hardcoded format gate survived'
ok('no hardcoded vertical-only gates remain')

print('\n[Manual is a UI state, never a crop_mode]')
assert "crop_mode = 'manual'" not in UI and '"manual"' not in UI.replace('toggleManualReframe', '')
ok('manual is never sent as a crop_mode', 'backend only accepts fit/fill')
assert 'function toggleManualReframe' in UI and 'function isManuallyFramed' in UI
ok('Manual toggles a hint and derives from actual framing')

print('\n%d assertions passed.' % PASS)
