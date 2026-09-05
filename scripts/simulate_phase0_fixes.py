"""Phase 0 regression suite — the six fixes from the 5 Sep 2026 forensic audit.

Each block below pins the DEFECT, not just the current shape of the code, so a
future edit that reintroduces the old behaviour fails here rather than in
production.

1. get_brand() returned soft-deleted brands. Ownership was enforced, existence
   was not, so a deleted brand stayed renderable.
2. "Use defaults" set status='skipped' and fired no render at all, while the
   button and its toast both promised one.
3. processAllBrands() reset in-flight items from 'processing' to 'pending',
   clearing fireRenderForItem's double-fire guard — a second render and a
   second credit for the same item.
4. A brand name went unchecked into the branded output path, which is then
   passed to os.makedirs(). '../../x' escaped the storage root.
5. /api/preview/extract-frame searched the disk before checking ownership, so
   the user_id filter below it was effectively unreachable.
6. /portal/downloader_dashboard batched every brand into one 1-credit request
   and reported every success as a failure.
7. Filesystem paths were interpolated raw into the FFmpeg filtergraph, so on
   Windows movie='C:\\...' parsed as the filename 'C' and every brand render
   failed. (Found by running the app locally, 5 Sep 2026.)
8. Polling reported every render failure as "job lost after server restart",
   discarding the real FFmpeg error that was in the response.

Real functions, AST-extracted from source and executed against stubs. The
get_brand block runs its real SQL against an in-memory SQLite. No Flask, no
project database, no FFmpeg, no browser:

    python scripts/simulate_phase0_fixes.py
"""
import ast
import io
import os
import re
import sqlite3
from contextlib import contextmanager

APP = io.open(os.path.join('portal', 'app.py'), encoding='utf-8').read()
DB = io.open(os.path.join('portal', 'database.py'), encoding='utf-8').read()
VP = io.open(os.path.join('portal', 'video_processor.py'), encoding='utf-8').read()
UI = io.open(os.path.join('portal', 'templates', 'clean_dashboard.html'),
             encoding='utf-8').read()

def banner_prefixes():
    """The real banner-prefix tuple, read from the module rather than
    duplicated here, so this suite cannot drift from what the code filters.
    Resolved lazily: if the helper is missing, section 8 should fail with a
    sentence that says so, not the whole suite at import with a ValueError."""
    assert '_FFMPEG_BANNER_PREFIXES' in VP, \
        'video_processor.py has no _FFMPEG_BANNER_PREFIXES - the banner filter is gone'
    ns = {}
    exec(compile(VP[VP.index('_FFMPEG_BANNER_PREFIXES'):VP.index('def ffmpeg_error_summary')],
                 '<banner>', 'exec'), ns)
    return ns['_FFMPEG_BANNER_PREFIXES']

PASS = 0


def ok(label, extra=''):
    global PASS
    PASS += 1
    print('  ok  %-58s %s' % (label, extra))


def extract(source, name, namespace):
    """Compile one real top-level function into `namespace` and return it."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            mod = ast.Module(body=[node], type_ignores=[])
            exec(compile(mod, '<%s>' % name, 'exec'), namespace)
            return namespace[name]
    raise AssertionError('function not found in source: %s' % name)


# ------------------------------------------------------- 1. get_brand ------
print('\n[1. a deleted brand is not a fetchable brand]')

CONN = sqlite3.connect(':memory:')
CONN.row_factory = sqlite3.Row
CONN.execute('''CREATE TABLE brands (
    id INTEGER PRIMARY KEY, name TEXT, display_name TEXT, user_id INTEGER,
    is_system INTEGER DEFAULT 0, is_locked INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1, logo_path TEXT, watermark_path TEXT)''')
# 1: live brand.  2: soft-deleted (delete_brand sets is_active = 0).
# 3: another user's live brand.  4: a live SYSTEM brand.
CONN.executemany(
    'INSERT INTO brands (id,name,display_name,user_id,is_system,is_active,logo_path)'
    ' VALUES (?,?,?,?,?,?,?)',
    [(1, 'ScotlandWTF', 'ScotlandWTF', 7, 0, 1, '/x/logo.png'),
     (2, 'DeletedWTF', 'DeletedWTF', 7, 0, 0, '/x/logo.png'),
     (3, 'SomeoneElse', 'SomeoneElse', 99, 0, 1, '/x/logo.png'),
     (4, 'SystemBrand', 'SystemBrand', None, 1, 1, '/x/logo.png')])
CONN.commit()


@contextmanager
def get_connection():
    yield CONN


ns = {'get_connection': get_connection}
get_brand = extract(DB, 'get_brand', ns)

assert get_brand(brand_id=1, user_id=7) is not None
ok('a live brand is still returned by id')
assert get_brand(name='ScotlandWTF', user_id=7) is not None
ok('a live brand is still returned by name')
assert get_brand(brand_id=4, user_id=7) is not None
ok('a live system brand is still returned')

assert get_brand(brand_id=2, user_id=7) is None, 'soft-deleted brand returned by id'
ok('a soft-deleted brand is NOT returned by id', 'the defect')
assert get_brand(name='DeletedWTF', user_id=7) is None, 'soft-deleted brand returned by name'
ok('a soft-deleted brand is NOT returned by name', 'the defect')
assert get_brand(brand_id=2) is None, 'soft-deleted brand returned by id, no user_id'
assert get_brand(name='DeletedWTF') is None, 'soft-deleted brand returned by name, no user_id'
ok('both no-user_id branches reject it too')

# Ownership must survive the fix.
assert get_brand(brand_id=3, user_id=7) is None, 'ownership check was weakened'
ok('another user\'s brand is still refused')

# is_active must keep meaning DELETED, not the future downgrade lock.
assert 'is_locked' not in DB[DB.index('def get_brand('):DB.index('def get_all_brands(')], \
    'get_brand now reasons about is_locked — locked is not deleted'
ok('get_brand does not conflate is_active with the future lock state')

# The reactivation path must still be able to see deleted rows.
assert 'is_active = 0' in DB[DB.index('def find_inactive_brand('):DB.index('def create_brand(')]
ok('find_inactive_brand still finds soft-deleted rows for reactivation')


# --------------------------------------------- 2. "Use defaults" renders ---
print('\n[2. "Use defaults" produces an output]')

body = UI[UI.index('async function useDefaultsAndNext()'):]
body = body[:body.index('\n// Back-compat alias')]

assert 'fireRenderForItem(current)' in body, '"Use defaults" still fires no render'
ok('it calls the same single-render path as Approve', 'the defect')
assert "current.userEdited = false" in body
ok('it clears userEdited so the server uses the stored brand config')
assert 'getFormatDefaults(' in body
ok('overrides are reset to the brand defaults for that format')
assert "'skipped'" not in body.split('if (current.status ===')[0], \
    'the item is still being marked skipped before anything else'
assert "current.status = 'skipped'" not in body, 'it still marks the item skipped'
ok('it no longer marks the item skipped')
assert 'useDefaultsAndNext()' in UI[UI.index('Use defaults') - 400:UI.index('Use defaults') + 40], \
    'the button is not wired to the corrected handler'
ok('the Use defaults button is wired to it')

# Charge-on-success is a per-request property of the server; assert the client
# still submits exactly one render per item and nothing here refunds anything.
assert body.count('fireRenderForItem(') == 1
ok('exactly one render is fired, not a batch')

# 'skipped' now has no producer, but every reader must remain so that items
# already persisted as skipped in localStorage still display.
assert "status === 'skipped'" in UI
ok('existing skipped items are still handled on read')


# ------------------------------------- 3. no double-charge on live renders ---
print('\n[3. a render in flight is never re-fired]')

assert 'const _inFlightRenderItems = new Set();' in UI
ok('in-flight items are tracked for the life of the page session')

wrapper = UI[UI.index('async function fireRenderForItem(item) {'):
             UI.index('async function _fireRenderForItemInner(item) {')]
assert wrapper.index("item.status === 'processing'") < wrapper.index('_inFlightRenderItems.add')
ok('the double-fire guard runs BEFORE the item is recorded')
assert '_inFlightRenderItems.add(item)' in wrapper and 'finally' in wrapper \
    and '_inFlightRenderItems.delete(item)' in wrapper
ok('the item is removed in a finally, so a failure cannot strand it')

reset = UI[UI.index('// Fix A: Reset stale'):]
reset = reset[:reset.index('persistJobItems();')]
assert "q.status === 'processing' && !_inFlightRenderItems.has(q)" in reset, \
    'the stale reset can still clear a live render'
ok('the stale-processing reset skips anything running now', 'the defect')
assert "q.status = 'pending'" in reset
ok('genuinely stranded items are still recovered after a reload')


# --------------------------------------------- 4. output paths are safe ----
print('\n[4. a brand name cannot escape the storage root]')

vns = {'os': os, 're': __import__('re'),
       '_SAFE_SEGMENT_RE': __import__('re').compile(r'[^A-Za-z0-9._-]+')}
safe_path_segment = extract(VP, 'safe_path_segment', vns)
assert_within = extract(VP, 'assert_within', vns)

for hostile in ('../../x', '..', '../..', 'a/b', '..\\..\\a', 'C:\\windows\\x',
                '.', './../x', '\x00evil'):
    seg = safe_path_segment(hostile, 'brand')
    assert '/' not in seg and '\\' not in seg and '\x00' not in seg
    assert seg not in ('.', '..') and not seg.startswith('.')
    assert os.path.basename(seg) == seg, 'segment is not a single path component'
ok('hostile names collapse to one inert segment', '9 inputs')

# Every brand name in production today is alphanumeric — nothing may change.
for real in ('AIWTF', 'ScotlandWTF', 'TheWestWTF', 'DarkHumourWTF', 'USA',
             'NorthernIreland', 's', 'Teat', 'Test', 'WTF', 'England'):
    assert safe_path_segment(real, 'brand') == real, real
ok('real brand names pass through byte-identical', '11 names')

root = os.path.join(os.sep, 'var', 'data', 'storage', 'outputs')
assert_within(root, os.path.join(root, 'v1_ScotlandWTF_vertical_9_16.mp4'))
ok('a normal output path is accepted')
for escape in ('..', os.path.join('..', '..', 'etc'), os.path.join('..', 'raw')):
    try:
        assert_within(root, os.path.join(root, escape, 'x.mp4'))
    except ValueError:
        pass
    else:
        raise AssertionError('assert_within let %r through' % escape)
ok('an escaping path is refused even if the sanitiser is bypassed')

# The render path must actually USE both, and check before it writes.
pb = VP[VP.index('    def process_brand('):]
pb = pb[:pb.index('filter_complex = self.build_filter_complex')]
assert 'safe_path_segment(brand_name' in pb and 'safe_path_segment(video_id' in pb
ok('process_brand builds the filename from safe segments', 'the defect')
assert pb.index('assert_within(self.output_dir, output_path)') < pb.index('work_path ='), \
    'containment is checked after the work path is derived'
ok('containment is asserted before any path is derived from it')
# Match the CALL, not the prose: the comment above it also says os.makedirs.
assert pb.index('assert_within(self.output_dir, output_path)') \
    < pb.index('os.makedirs(os.path.dirname(output_path)'), \
    'os.makedirs runs before the containment check'
ok('containment is asserted before os.makedirs')

# The legacy preview-asset routes go through the same door.
for route in ('def get_watermark_preview(', 'def get_logo_preview('):
    blk = APP[APP.index(route):]
    blk = blk[:blk.index('@app.route', 10)]
    assert 'safe_path_segment(' in blk, route
ok('the legacy watermark/logo preview routes sanitise too')


# ------------------------------------------- 5. extract-frame ownership ----
print('\n[5. frames are only extracted from your own files]')

ef = APP[APP.index("@app.route('/api/preview/extract-frame'"):]
ef = ef[:ef.index('@app.route', 10)]

assert 'user_can_download_filename(req_user_id, filename)' in ef, 'no ownership check'
ok('the route calls the same helper the download route uses', 'the defect')
assert ef.index('user_can_download_filename(req_user_id, filename)') < ef.index('search_paths = ['), \
    'ownership is checked after the disk search'
ok('ownership is checked BEFORE the filesystem is touched')
assert ef.index('user_can_download_filename(req_user_id, filename)') < ef.index('os.path.exists'), \
    'the disk is probed before ownership is established'
ok('no os.path.exists runs before the gate')
assert '403' in ef[ef.index('user_can_download_filename'):ef.index('search_paths = [')]
ok('cross-tenant access returns 403')
assert '@login_required' in APP[APP.index("@app.route('/api/preview/extract-frame'"):
                                APP.index('def extract_frame')]
ok('authentication is unchanged — @login_required still runs first')

# The helper must stay deny-by-default, or the 403 becomes an existence oracle.
ucdf = DB[DB.index('def user_can_download_filename('):]
_end = ucdf.find('\ndef ', 10)          # it is the last function in the module
ucdf = ucdf if _end == -1 else ucdf[:_end]
assert 'return False' in ucdf
ok('the helper still denies by default')


# ------------------------------------- 6. the alternate render path is gone --
print('\n[6. there is one submit surface, not two]')

dd = APP[APP.index("@app.route('/portal/downloader_dashboard')"):]
dd = dd[:dd.index('@app.route', 10)]
assert 'render_template' not in dd, 'the retired page still renders'
ok('the route no longer serves the page', 'the defect')
assert "redirect(url_for('brand_video'))" in dd
ok('it redirects to Create, as /portal/download does')

# The one legitimate submit site must still send exactly one brand per request,
# because process_brands charges one credit per REQUEST.
assert APP.count('spend_credits(user_id, 1, _allowance)') == 1
ok('still exactly one charge site in the render path')
fire = UI[UI.index('async function _fireRenderForItemInner(item) {'):]
fire = fire[:fire.index('async function processCurrentBrand()')]
assert 'brand_ids:     [item.brand_id],' in fire
ok('the live submit path still sends one brand per request')


# ------------------------------------- 7. filtergraph paths are escaped ----
print('\n[7. a Windows path cannot break the filtergraph]')

vns2 = {'os': os, 're': __import__('re')}
ffmpeg_filter_path = extract(VP, 'ffmpeg_filter_path', vns2)

win = r"C:\Users\PC\Desktop\wtf_brandr_app\portal\private\storage\brands\1\1\logo.png"
esc = ffmpeg_filter_path(win)
# NOT "no backslashes": the helper deliberately ADDS them as escape
# characters. What must be gone is the backslash as a PATH SEPARATOR.
assert '/Users/PC/' in esc, esc
ok('path separators become forward slashes')
assert not re.search(r'\\(?![:\'])', esc), 'a backslash remains that is not an escape'
ok('every remaining backslash is an escape for : or \'')
assert esc.startswith(r'C\:'), esc
ok('the drive colon is escaped', esc[:12])
# The exact failure mode: an UNescaped ':' ends the filename at 'C'.
_re = re
assert not _re.search(r'(?<!\\):', esc), 'an unescaped colon remains -> filename truncates to C'
ok('no unescaped colon remains anywhere', 'the defect')
assert ffmpeg_filter_path("/tmp/a'b.png") == "/tmp/a\\'b.png"
ok("a quote in the path is escaped too")

# Production safety: a normal Linux path must be returned BYTE-IDENTICAL,
# because the emitted command is the normalize cache key.
for linux in ('/var/data/storage/brands/1/1/logo_normalized.png',
              '/var/data/storage/brands/12/34/watermark.png',
              '/tmp/with space/logo.png'):
    assert ffmpeg_filter_path(linux) == linux, linux
ok('Linux paths are unchanged, so cache keys are not invalidated', '3 paths')

# Every movie= site must go through the helper -- a new one added raw reopens it.
raw = _re.findall(r"movie='\{(?!ffmpeg_filter_path)", VP)
assert not raw, '%d movie= interpolation(s) bypass ffmpeg_filter_path' % len(raw)
ok('no movie= interpolation bypasses the helper')
assert VP.count('ffmpeg_filter_path(') >= 8   # 7 call sites + the definition
ok('all seven movie= sites use it', '%d references' % VP.count('ffmpeg_filter_path('))


# ---------------------------- 8. real render errors reach the user ---------
print('\n[8. a failed render says why it failed]')

ffmpeg_error_summary = extract(VP, 'ffmpeg_error_summary',
                               {'os': os, '_FFMPEG_BANNER_PREFIXES': banner_prefixes()})
REAL = """ffmpeg version 8.1 Copyright (c) 2000-2025 the FFmpeg developers
built with gcc 15.2.0 (Rev8, Built by MSYS2 project)
configuration: --enable-gpl --enable-libx264 --enable-libaom --enable-mediafoundation
  libavutil      60.  5.100 /  60.  5.100
  libavfilter    11. 14.100 / 11. 14.100
[Parsed_movie_0 @ 00000200a7f2c140] Failed to avformat_open_input 'C'
[AVFilterGraph @ 00000200a7f29380] Error initializing filters
Error : No such file or directory"""
summary = ffmpeg_error_summary(REAL)
assert 'avformat_open_input' in summary, 'the diagnosis was dropped'
ok('the actual cause survives')
assert '--enable-' not in summary, 'the build banner is still being reported'
ok('the build banner does not', 'the defect')
assert 'libavfilter' not in summary and 'ffmpeg version' not in summary
ok('version and library lines are dropped too')
assert ffmpeg_error_summary('') == ''
ok('empty stderr yields empty, not a crash')
assert ffmpeg_error_summary('configuration: --enable-gpl').strip() != ''
ok('banner-only stderr still returns something rather than nothing')

# Client: 404 and "the server told us it failed" must be different branches.
for label, marker in (('live poll', "console.warn('[FIRE] Job lost:"),
                      ('resume path', "console.warn('[RESUME] Job lost:")):
    blk = UI[UI.index(marker) - 700: UI.index(marker) + 1400]
    assert 'pollRes.status === 404 || pollData.error' not in blk, \
        '%s still collapses 404 and a real error into one branch' % label
    ok('%s no longer collapses the two cases' % label, 'the defect')
    assert 'if (pollRes.status === 404)' in blk, label
    ok('%s keeps a dedicated 404 = lost-job branch' % label)
    assert re.search(r'item\.error\s*=\s*pollData\.error;', blk), label
    ok('%s surfaces the server error verbatim' % label)

# The restart wording must never be attached to a real error again.
restart_msg = "Render job lost after server restart. Please retry."
for m in _re.finditer(_re.escape(restart_msg), UI):
    before = UI[max(0, m.start() - 400): m.start()]
    assert 'pollRes.status === 404' in before or 'jobId' in before, \
        'the restart message is reachable from a non-404 path'
ok('the restart message is only reachable from a 404')

print('\n%d assertions passed.' % PASS)
