"""The narrow seam that makes composition reachable -- and nothing more.

Slice B exists to prove composition in production, not to be the finished
bookend system. So this suite asserts two things with equal weight:

  1. the plumbing genuinely reaches process_brand, ownership-scoped and
     fail-fast, and
  2. it has NOT grown policy -- no tier entitlement, no Explorer default, no
     enabled/scope/tier_required columns, no intro_asset_id, no credit changes.

The second half matters as much as the first. Infrastructure commits that
quietly acquire product policy are how a seam becomes a feature nobody decided
to ship.

No Flask, no DB, no FFmpeg, no network:
    python scripts/simulate_bookend_slice.py
"""
import io
import os
import re

APP = io.open(os.path.join('portal', 'app.py'), encoding='utf-8').read()
DB = io.open(os.path.join('portal', 'database.py'), encoding='utf-8').read()
CFG = io.open(os.path.join('portal', 'config.py'), encoding='utf-8').read()
VP = io.open(os.path.join('portal', 'video_processor.py'), encoding='utf-8').read()

PASS = 0


def ok(label, extra=''):
    global PASS
    PASS += 1
    print('  ok  %-56s %s' % (label, extra))


print('\n[storage: assets are not source videos]')
assert "BOOKENDS_DIR = os.path.join(STORAGE_ROOT, 'bookends')" in CFG
ok('assets live in their own directory', 'never shown as Library sources')
assert re.search(r'for directory in \[[^\]]*BOOKENDS_DIR', CFG)
ok('the directory is created at startup')

print('\n[schema is minimal, and must STAY minimal]')
schema = DB[DB.index('CREATE TABLE IF NOT EXISTS bookend_assets'):]
schema = schema[:schema.index(')')+1]
for col in ('id', 'user_id', 'display_name', 'file_path', 'created_at'):
    assert col in schema, col
ok('five columns only', 'id, user_id, display_name, file_path, created_at')

# The guard that keeps slice B a seam rather than a feature. Conformed
# per-format variants are derived on disk by conform_bookend and keyed on the
# conform command, so no variant table is needed either.
for forbidden in ('tier_required', 'default_for_tier', 'enabled', 'scope',
                  'intro_asset_id', 'asset_format_variants', 'is_default'):
    assert forbidden not in schema, 'policy column crept in: %s' % forbidden
ok('no policy columns', 'tier/enabled/scope/default all absent')
assert 'intro_asset_id' not in APP
ok('no intro_asset_id anywhere', 'outro only, as scoped')

print('\n[ownership is enforced in SQL, not in a caller]')
fn = DB[DB.index('def get_bookend_asset('):DB.index('def list_bookend_assets(')]
assert 'WHERE id = ? AND user_id = ?' in fn
ok('get_bookend_asset filters by user_id',
   'naming another account asset id must not work')
lst = DB[DB.index('def list_bookend_assets('):]
assert 'WHERE user_id = ?' in lst.split('def ')[0] + lst[:400]
ok('list is scoped to the caller')

print('\n[upload reuses the media gate rather than trusting the extension]')
up = APP[APP.index('def upload_bookend_asset():'):APP.index('def list_bookend_assets_api():')]
assert 'probe_media(file_path)' in up
ok('bookend uploads are content-validated')
i_probe, i_save = up.index('probe_media('), up.index('save_bookend_asset(')
assert i_probe < i_save
ok('probed BEFORE the row is written')
assert 'os.remove(file_path)' in up[i_probe:i_save]
ok('a rejected asset is deleted', 'nothing stranded on a 5 GB disk')
assert "'code': 'INVALID_MEDIA'" in up and "'error': f\"Sorry" in up
ok('human sentence in `error`, code in `code`', 'same convention as upload')
assert 'MAX_UPLOAD_SIZE' in up and 'ALLOWED_EXTENSIONS' in up
ok('size and extension checks retained')

print('\n[resolution: fail fast, before any work begins]')
route = APP[APP.index("@app.route('/api/videos/process_brands'"):]
route = route[:route.index('daemon=True')]
i_resolve = route.index('outro_asset_id')
i_thread = route.index('threading.Thread')
assert i_resolve < i_thread
ok('the asset is resolved before the job is queued',
   'a bad id cannot blow up inside a background thread')
# These assert the PROPERTY, not where it lives. An earlier version pinned the
# ownership check to an inline block in process_brands and broke the moment the
# logic moved into resolve_outro_for_render -- while the property itself still
# held. Location is not the contract.
resolver = APP[APP.index('def resolve_outro_for_render('):]
resolver = resolver[:resolver.index(chr(10) + 'def ')]
assert 'get_bookend_asset(asset_id, user_id)' in resolver
ok('resolution is ownership-scoped', 'wherever the resolver lives')
assert "'Outro asset not found'" in resolver and '404' in resolver
ok('an unknown or unowned id is refused')
assert 'os.path.isfile' in resolver
ok('a row pointing at a missing file is refused too')
assert 'return None, None' in resolver
ok('resolves to nothing when nothing applies', 'the pre-bookend path exactly')

print('\n[the plumbing actually reaches the renderer]')
assert 'source_edit=None, outro_path=None' in APP
ok('_do_brand_render takes outro_path, defaulting to None')
assert re.search(r'process_brand\(\s*merged_config, video_id=video_id,\s*'
                 r'output_format=output_format,\s*outro_path=outro_path\)', APP)
ok('it is forwarded to process_brand')
assert 'intro_path: Optional[str] = None' in VP
ok('process_brand still accepts intro_path too', 'unused by this slice')

print('\n[asset STORAGE stays free of policy]')
# HISTORICAL NOTE. These guards originally asserted that the whole seam carried
# no policy at all, which was true of slice B and deliberately stopped being true
# in 0b97163, where the Explorer floor and the paid-tier preference were added on
# purpose. Asserting "no Explorer default" now would claim something false about
# the system, so the guard has been narrowed to what still holds and still
# matters: policy lives in the RESOLVER, never in asset storage or upload.
for forbidden in ('tier_required', 'default_for_tier', 'outro_enabled'):
    assert forbidden not in APP, forbidden
ok('no tier columns crept into the schema', 'entitlement stays in config.py')
upload_and_list = APP[APP.index('def upload_bookend_asset():'):
                      APP.index('def list_bookend_assets_api():')]
assert 'Explorer' not in upload_and_list and 'tier' not in upload_and_list
ok('uploading an asset knows nothing about tiers', 'storage is not policy')
assert 'keep_brandr_outro' not in upload_and_list
ok('nor about the Brandr-outro preference')
# The charge site is still exactly one, still after validation.
assert APP.count('spend_credits(user_id, 1, _allowance)') == 1
ok('still exactly one charge site', 'no new credit semantics')
assert 'MAX_RENDERS_PER_ACCOUNT' in APP and 'RENDER_QUEUE_TIMEOUT = 1800' in APP
ok('queueing and concurrency untouched')

print('\n%d assertions passed.' % PASS)
