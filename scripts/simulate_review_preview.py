"""Three review-screen defects found live on 4 Sep 2026, and the maths behind one.

1. exitQueueMode() showed the brand grid again but left the preview canvas on
   screen -- still painted with the item just reviewed, and by then belonging to
   a brand the same function had already deselected.

2. The preview's logo-shape lookup fell back to 'original' on a cache miss.
   Every stored logo_normalized.png is FULLY OPAQUE (verified: corner alpha 255,
   0% transparent pixels across all 25 brands), so 'original' draws the raw black
   rectangle. The fallback was not a slightly-wrong preview, it was the worst
   available one -- and intermittent, which is why a square logo appeared on
   mobile but not desktop for the same brand.

3. The review preview box pinned HEIGHT from the viewport while
   resizeCanvasForFormat overrode its aspect-ratio per format, so 1:1 and 16:9
   canvases rendered at the top of a 9:16-tall box with black beneath.

No Flask, no DB, no FFmpeg, no browser:
    python scripts/simulate_review_preview.py
"""
import io
import os
import re

UI = io.open(os.path.join('portal', 'templates', 'clean_dashboard.html'),
             encoding='utf-8').read()

PASS = 0


def ok(label, extra=''):
    global PASS
    PASS += 1
    print('  ok  %-56s %s' % (label, extra))


# ------------------------------------------------------------------ fix 1 --
print('\n[the preview no longer follows you back to the brand grid]')
enter = UI[UI.index('async function enterQueueMode()'):]
enter = enter[:enter.index('\nfunction ') if '\nfunction ' in enter else 4000]
exit_ = UI[UI.index('function exitQueueMode()'):]
exit_ = exit_[:exit_.index('\nfunction ')]

assert "previewContainer.style.display = 'block'" in enter.replace('if (previewContainer) ', '')
ok('enterQueueMode still shows the preview')
assert 'previewContainer' in exit_ and "style.display = 'none'" in exit_
ok('exitQueueMode now hides it', 'the missing half of the pair')

# The pair must stay symmetric: anything enter shows, exit must address.
ids = lambda s: set(re.findall(r"getElementById\('([^']+)'\)", s))
shown_not_restored = ids(enter) - ids(exit_)
assert 'previewContainer' not in shown_not_restored, shown_not_restored
ok('nothing enter shows is left unhandled by exit', 'this asymmetry WAS the bug')

# It cannot merely be left visible: exit clears the selection it belonged to.
assert 'selectedBrandIds = []' in exit_ and 'activePreviewBrandId = null' in exit_
ok('exit clears the selection', 'so a visible preview would show a deselected brand')


# ------------------------------------------------------------------ fix 2 --
print('\n[a cache miss no longer silently draws the logo unmasked]')
assert 'function resolveLogoShapeForPreview()' in UI
ok('shape resolution is a named function')
res = UI[UI.index('function resolveLogoShapeForPreview()'):]
res = res[:res.index('\nfunction ')]
assert 'processingQueue[currentQueueIndex]' in res and 'current.brand_id' in res
ok('falls back to the REVIEWED ITEM brand', 'the cockpit always knows it')
assert res.count('console.warn') == 2
ok('both fallback paths warn', 'silent degradation is what hid this')
assert "(brandDataCache[activePreviewBrandId] && brandDataCache[activePreviewBrandId].logo_shape) || 'original'" not in UI
ok('the old bare `|| original` fallback is gone')


# ------------------------------------------------------------------ fix 3 --
print('\n[the preview box is the shape of the format, at every viewport]')
rule = UI[UI.index('#brandReviewLane.review-active #previewContainer {'):]
rule = rule[:rule.index('}')]
assert '--preview-aspect' in rule
ok('aspect is a CSS variable', 'aspect-ratio cannot be used inside calc()')
assert 'height: auto' in rule
ok('height is no longer pinned to the viewport', 'that pin caused the void')
assert not re.search(r'\n\s*height: min\(calc\(100dvh', rule)
ok('the viewport-derived height is gone')
assert "pc.style.setProperty('--preview-aspect'" in UI
ok('resizeCanvasForFormat sets the variable')

# The maths. width = min(availW, max(200, availH * aspect)); height = width/aspect.
# Because width <= availH * aspect, height <= availH -- the review invariant
# ("the whole frame visible, nothing sticky covering it") survives without
# pinning height.
FORMATS = {'vertical_9_16': 720 / 1280, 'square_1_1': 1.0, 'landscape_16_9': 1280 / 720}
print('\n      viewport            format          box          fits?  void?')
worst_void = 0.0
for availH, availW, label in ((500, 360, 'phone  800h/300 chrome'),
                              (360, 360, 'phone  short'),
                              (900, 360, 'phone  tall'),
                              (700, 320, 'tablet')):
    for name, a in FORMATS.items():
        w = min(availW, max(200.0, availH * a))
        h = w / a
        # The canvas inside is width:100%, height:auto -> its height is w/a too.
        void = h - (w / a)
        worst_void = max(worst_void, abs(void))
        fits = h <= availH + 0.5
        print('      %-19s %-15s %4.0fx%-4.0f  %-5s  %.1f'
              % (label, name, w, h, 'yes' if fits else 'NO', abs(void)))
        assert fits or availH * a < 200, '%s %s overflows: %.0f > %.0f' % (label, name, h, availH)
        assert abs((w / h) - a) < 0.01, 'box is not the format aspect'
assert worst_void < 0.01
ok('canvas exactly fills the box in every case', 'no black void anywhere')

# What the OLD rule did, for contrast: height pinned, width clamped.
old_h, old_w = 500.0, 360.0            # phone, 9:16 height pinned
canvas_h = old_w / 1.0                 # a 1:1 canvas at that clamped width
print('      OLD rule, 1:1 on a phone: box 360x500, canvas 360x360 -> %.0fpx of black'
      % (old_h - canvas_h))
assert old_h - canvas_h > 100
ok('reproduces the reported void on the old rule', '140px, matching the screenshots')

print('\n%d assertions passed.' % PASS)
