"""Intro/outro composition: the contract, and the checks that catch a wrong artifact.

WHY THE CHECKS LOOK PARANOID. The junction experiment (4 Sep 2026) established
that stream-copy concat is reliable -- 227 frames = 150 + 77 exactly, full decode
with zero stderr -- but that it does NOT refuse mismatched inputs. It silently
produces a plausible file:

    48kHz branded + 44.1kHz outro, not conformed
        valid container, clean decode, exact dimensions, correct TOTAL duration
        ... and an audio stream 0.58s shorter than its video, the outro playing
        ~8.8% fast. RMS across the junction looked perfectly continuous.

Every other check passes that file. Only comparing the audio and video stream
durations against each other catches it -- and it must be bounded in BOTH
directions, since a long audio stream is the same fault mirrored.

Part 1 runs the REAL verify_composition against stubbed probes so every rejection
branch is reachable without FFmpeg. Part 2 asserts the wiring in process_brand.
The real-media matrix runs separately (it needs FFmpeg); this suite stays
FFmpeg-free like the rest.

    python scripts/simulate_composition.py
"""
import ast
import io
import os
import subprocess

VP = io.open(os.path.join('portal', 'video_processor.py'), encoding='utf-8').read()

PASS = 0


def ok(label, extra=''):
    global PASS
    PASS += 1
    print('  ok  %-56s %s' % (label, extra))


# --- load the real verifier against stubbed probing --------------------------
class FakeCompleted(object):
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class FakeSubprocess(object):
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self):
        self.decode_result = FakeCompleted(0, '', '')

    def run(self, cmd, **kw):
        return self.decode_result


fake = FakeSubprocess()
SUMMARY = {'video': None, 'audio': None, 'format': {}}


def _stub_summary(path):
    return SUMMARY['video'], SUMMARY['audio'], SUMMARY['format']


ns = {'os': os, 'subprocess': fake, 'FFMPEG_BIN': 'ffmpeg', 'FFPROBE_BIN': 'ffprobe',
      '_stream_summary': _stub_summary}
WANT = ('CONFORM_CACHE_VERSION', 'CONCAT_AUDIO_RATE', 'CONCAT_AUDIO_CHANNELS',
        'CONCAT_AUDIO_BITRATE', 'CONCAT_FPS', 'COMPOSE_DURATION_TOLERANCE',
        'COMPOSE_AV_SKEW_TOLERANCE', 'COMPOSE_ENCODE_TIMEOUT',
        'COMPOSE_DECODE_TIMEOUT', 'CompositionError', '_as_seconds',
        'verify_composition')
for node in ast.parse(VP).body:
    nm = None
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        nm = node.name
    elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        nm = node.targets[0].id
    if nm in WANT:
        exec(ast.get_source_segment(VP, node), ns)

verify = ns['verify_composition']
CompositionError = ns['CompositionError']
RATE, CHANS = ns['CONCAT_AUDIO_RATE'], ns['CONCAT_AUDIO_CHANNELS']
DUR_TOL, SKEW_TOL = ns['COMPOSE_DURATION_TOLERANCE'], ns['COMPOSE_AV_SKEW_TOLERANCE']

print('audio contract : %d Hz x%d %s' % (RATE, CHANS, ns['CONCAT_AUDIO_BITRATE']))
print('tolerances     : duration %.2fs, a/v skew %.2fs' % (DUR_TOL, SKEW_TOL))


def given(v_dur=7.577, a_dur=None, w=1280, h=720, audio=True, video=True,
          decode_ok=True):
    SUMMARY['video'] = ({'width': w, 'height': h, 'duration': str(v_dur)}
                        if video else None)
    SUMMARY['audio'] = ({'duration': str(v_dur if a_dur is None else a_dur),
                         'sample_rate': str(RATE), 'channels': CHANS}
                        if audio else None)
    SUMMARY['format'] = {'duration': str(v_dur)}
    fake.decode_result = (FakeCompleted(0, '', '') if decode_ok
                          else FakeCompleted(1, '', 'Invalid NAL unit size'))


def rejects(label, **kw):
    given(**kw)
    try:
        verify('x.mp4', 7.577, 1280, 720)
    except CompositionError as e:
        return str(e)
    raise AssertionError('%s was ACCEPTED' % label)


print('\n[a correctly composed artifact is accepted]')
given()
meta = verify('x.mp4', 7.577, 1280, 720)
assert meta['width'] == 1280 and meta['height'] == 720
ok('valid composition accepted', '%dx%d %.3fs' % (meta['width'], meta['height'],
                                                  meta['duration']))

print('\n[THE BUG THE EXPERIMENT FOUND: audio that does not track video]')
# 48kHz branded + 44.1k outro produced exactly this: everything else correct.
msg = rejects('short audio', v_dur=7.567, a_dur=6.983)
assert 'skew' in msg, msg
ok('audio 0.58s SHORT rejected', 'the real 48kHz failure')
# Bounded BOTH ways -- checking only the floor would accept the mirror image.
msg = rejects('long audio', v_dur=7.567, a_dur=8.400)
assert 'skew' in msg, msg
ok('audio 0.83s LONG rejected', 'the same fault reversed')
given(v_dur=7.577, a_dur=7.577 - (SKEW_TOL * 0.5))
verify('x.mp4', 7.577, 1280, 720)
ok('small skew within tolerance still accepted', 'not hair-trigger')

print('\n[a missing segment shows up as duration]')
msg = rejects('short total', v_dur=5.000, a_dur=5.000)
assert 'segment is probably missing' in msg, msg
ok('composed 2.58s short rejected', 'the outro never made it in')
msg = rejects('long total', v_dur=10.500, a_dur=10.500)
assert 'segment is probably missing' in msg
ok('composed too LONG rejected', 'a segment added twice')
given(v_dur=7.577 + DUR_TOL * 0.5)
verify('x.mp4', 7.577, 1280, 720)
ok('normal concat drift accepted', 'experiment measured +0.024s')

print('\n[the other ways an artifact can be wrong]')
msg = rejects('no video', video=False)
assert 'no video stream' in msg
ok('missing video stream rejected')
msg = rejects('wrong dims', w=720, h=1280)
assert '720x1280' in msg and '1280x720' in msg, msg
ok('wrong dimensions rejected', 'format is a contract')
msg = rejects('no audio', audio=False)
assert 'lost its audio' in msg
ok('audio track dropped by concat rejected', 'the no-audio failure mode')
msg = rejects('bad decode', decode_ok=False)
assert 'does not decode cleanly' in msg
ok('shredded bitstream rejected', 'ffprobe alone would accept it')
msg = rejects('zero duration', v_dur=0.0, a_dur=0.0)
assert 'duration' in msg
ok('zero duration rejected')


# --- part 2: the wiring ------------------------------------------------------
print('\n[composition is wired where it belongs]')
body = VP[VP.index('def process_brand('):VP.index('def process_multiple_brands(')]

assert 'intro_path: Optional[str] = None' in body and 'outro_path: Optional[str] = None' in body
ok('process_brand accepts intro/outro paths')

assert 'if intro_path or outro_path:' in body
ok('no bookends means NO composition pass', 'existing path untouched')

i_valid = body.index('output_valid = self._validate_output(work_path)')
i_comp = body.index('compose_bookends(')
i_pub = body.index('os.replace(publish_path, output_path)')
assert i_valid < i_comp < i_pub, (i_valid, i_comp, i_pub)
ok('order: brand encode -> validate -> COMPOSE -> publish',
   'composing after publish would mutate a delivered file')

assert 'probe_dimensions(work_path)' in body
ok('bookends conform to MEASURED branded dimensions', 'not the requested ones')

comp_block = body[i_comp - 900:i_pub]
assert '_discard_work_file(work_path)' in comp_block
ok('a failed composition discards the branded intermediate')
assert 'if composed_path:' in body[i_pub:i_pub + 600]
ok('a successful composition discards it too', 'no orphan on a 5 GB disk')

print('\n[the concat contract]')
assert "'-c', 'copy'" in VP
ok('concat uses stream copy')
conform = VP[VP.index('def conform_branded_for_concat('):VP.index('def concat_copy(')]
assert "'-c:v', 'copy'" in conform
ok('branded video is NEVER re-encoded', 'only its audio, and only if needed')
assert "'-c:v', 'libx264'" not in conform
ok('no video encoder appears in the branded conform path')
assert 'return branded_path, False' in conform
ok('audio already on contract costs nothing', 'copied as-is')
assert 'anullsrc' in conform
ok('a silent branded segment gets synthesised audio')

bookend = VP[VP.index('def conform_bookend('):VP.index('def conform_branded_for_concat(')]
assert "'-ar', str(CONCAT_AUDIO_RATE)" in bookend and "'-ac', str(CONCAT_AUDIO_CHANNELS)" in bookend
ok('bookends are pinned to the audio contract')
assert 'anullsrc' in bookend
ok('a silent bookend gets audio too', 'or concat drops the other segment audio')
assert '_lock_for(' in bookend and 'os.replace(tmp, dest)' in bookend
ok('conform cache is locked and published atomically', 'same pattern as normalize')
assert '_conformed_path(asset_path, output_format' in bookend
ok('cache identity is per (asset, output_format)')

compose = VP[VP.index('def compose_bookends('):]
i_intro = compose.index('if intro_path:')
i_seg = compose.index('parts.append(segment)')
i_outro = compose.index('if outro_path:')
assert i_intro < i_seg < i_outro
ok('segment order is intro, branded, outro')
assert '_discard_work_file(out_path)' in compose
ok('a failed compose leaves no half-built artifact')

print('\n%d assertions passed.' % PASS)
