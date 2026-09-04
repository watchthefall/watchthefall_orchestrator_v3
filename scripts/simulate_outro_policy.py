"""Who gets the Brandr outro, and who can turn it off.

The composition engine has been live and production-proven since 9ea8fa7/af0376b;
what was missing was the policy deciding when to invoke it. This suite covers
that decision and nothing else.

The rule that matters most is the Explorer floor: free renders carry Brandr
branding, it is not a preference, and a request naming a different asset must not
beat it. Everything else is ordinary precedence.

Runs the REAL resolver, AST-extracted from portal/app.py and executed against
stubs. No Flask, no DB, no FFmpeg:

    python scripts/simulate_outro_policy.py
"""
import ast
import io
import os

APP = io.open(os.path.join('portal', 'app.py'), encoding='utf-8').read()
CFG = io.open(os.path.join('portal', 'config.py'), encoding='utf-8').read()
DB = io.open(os.path.join('portal', 'database.py'), encoding='utf-8').read()

PASS = 0


def ok(label, extra=''):
    global PASS
    PASS += 1
    print('  ok  %-54s %s' % (label, extra))


# --- stubs -------------------------------------------------------------------
ACCOUNT = {'tier': 'Explorer', 'founding': False, 'keep': False}
ASSETS = {}          # (asset_id, user_id) -> row
EXISTING_FILES = set()


class FakeResponse(object):
    def __init__(self, payload):
        self.payload = payload


def jsonify(payload):
    return FakeResponse(payload)


def get_user_tier(user_id):
    return ACCOUNT['tier']


def get_bookend_asset(asset_id, user_id):
    return ASSETS.get((asset_id, user_id))


class FakeConn(object):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        class R(object):
            def fetchone(self_inner):
                return {'founding_status': 1 if ACCOUNT['founding'] else 0,
                        'keep_brandr_outro': 1 if ACCOUNT['keep'] else 0}
        return R()


def get_connection():
    return FakeConn()


class FakeOsPath(object):
    def __getattr__(self, name):
        return getattr(os.path, name)

    def isfile(self, p):
        return p in EXISTING_FILES

    def basename(self, p):
        return os.path.basename(p)


class FakeOs(object):
    path = FakeOsPath()


BRANDR_OUTRO_FILES = {}


def brandr_outro_path(tier, founding_status=False):
    name = 'founder' if founding_status else BRANDR_OUTRO_FILES.get(tier)
    if not name:
        return None
    p = '/brandr_outros/%s.mp4' % name
    return p if p in EXISTING_FILES else None


ns = {'os': FakeOs(), 'jsonify': jsonify, 'get_user_tier': get_user_tier,
      'get_bookend_asset': get_bookend_asset, 'get_connection': get_connection}
for node in ast.parse(APP).body:
    if isinstance(node, ast.FunctionDef) and node.name in (
            '_account_outro_flags', 'resolve_outro_for_render'):
        exec(ast.get_source_segment(APP, node), ns)
# The resolver imports brandr_outro_path from .config at call time; pre-seed it.
ns['brandr_outro_path'] = brandr_outro_path
resolve = ns['resolve_outro_for_render']


class FakeConfigModule(object):
    brandr_outro_path = staticmethod(brandr_outro_path)


import sys, types
_pkg = types.ModuleType('portal')
_pkg.__path__ = []
_cfg = types.ModuleType('portal.config')
_cfg.brandr_outro_path = brandr_outro_path
sys.modules.setdefault('portal', _pkg)
sys.modules['portal.config'] = _cfg
ns['__package__'] = 'portal'
ns['__name__'] = 'portal.app'


def given(tier='Explorer', founding=False, keep=False):
    ACCOUNT.update(tier=tier, founding=founding, keep=keep)


BRANDR_OUTRO_FILES.update({'Explorer': 'explorer', 'Creator': 'creator',
                           'Studio': 'studio', 'Platinum': 'platinum',
                           'Elite': 'platinum'})
for n in ('explorer', 'creator', 'studio', 'platinum', 'founder'):
    EXISTING_FILES.add('/brandr_outros/%s.mp4' % n)
ASSETS[(7, 1)] = {'file_path': '/assets/mine.mp4', 'display_name': 'My Outro'}
EXISTING_FILES.add('/assets/mine.mp4')


print('\n[THE EXPLORER FLOOR — not a preference]')
given('Explorer')
path, err = resolve(1, None)
assert err is None and path == '/brandr_outros/explorer.mp4', (path, err)
ok('Explorer always gets the Brandr outro', 'no setting involved')

path, err = resolve(1, 7)
assert err is None and path == '/brandr_outros/explorer.mp4', (path, err)
ok('a requested asset does NOT beat the floor', 'free output carries Brandr branding')

given('Explorer', keep=True)
path, _ = resolve(1, None)
assert path == '/brandr_outros/explorer.mp4'
ok('the preference is irrelevant on Explorer')

given('Explorer', founding=True)
path, _ = resolve(1, None)
assert path == '/brandr_outros/founder.mp4', path
ok('a founding member sees the founder outro', 'whatever tier they hold')

print('\n[PAID TIERS — off unless asked for]')
given('Creator')
path, err = resolve(1, None)
assert path is None and err is None
ok('Creator with no preference gets NO outro', 'exactly the pre-bookend path')

given('Creator', keep=True)
path, _ = resolve(1, None)
assert path == '/brandr_outros/creator.mp4', path
ok('Creator who opts in gets the Brandr outro', 'tier-coloured')

given('Studio', keep=True)
path, _ = resolve(1, None)
assert path == '/brandr_outros/studio.mp4'
ok('Studio opt-in gets the Studio outro')

given('Creator')
path, _ = resolve(1, 7)
assert path == '/assets/mine.mp4', path
ok('Creator can use their own asset')

given('Creator', keep=True)
path, _ = resolve(1, 7)
assert path == '/assets/mine.mp4'
ok('their own asset beats the Brandr one', 'more specific wins')

given('Platinum', founding=True, keep=True)
path, _ = resolve(1, None)
assert path == '/brandr_outros/founder.mp4'
ok('founding Platinum opting in sees the founder outro')

print('\n[refusals and degradation]')
given('Creator')
path, err = resolve(1, 'abc')
assert path is None and err and err[1] == 400, err
ok('non-integer asset id rejected', '400')

path, err = resolve(1, 999)
assert path is None and err and err[1] == 404
ok("another account's asset id rejected", '404 — ownership-scoped')

ASSETS[(8, 1)] = {'file_path': '/assets/gone.mp4', 'display_name': 'Deleted'}
path, err = resolve(1, 8)
assert path is None and err and err[1] == 404
ok('a row pointing at a missing file rejected')

# A missing promotional asset must never fail a render.
EXISTING_FILES.discard('/brandr_outros/explorer.mp4')
given('Explorer')
path, err = resolve(1, None)
assert path is None and err is None
ok('missing Brandr asset renders WITHOUT it', 'never fail over our packaging')
EXISTING_FILES.add('/brandr_outros/explorer.mp4')


print('\n[the assets exist and match the concat contract]')
assert "'Explorer': 'outro_explorer_primary.mp4'" in CFG
ok('config maps every tier to an outro')
assert 'BRANDR_OUTRO_FOUNDER' in CFG
ok('founding members have their own')
d = os.path.join('portal', 'static', 'brandr_outros')
files = sorted(f for f in os.listdir(d)) if os.path.isdir(d) else []
assert len(files) == 5, files
ok('five outro assets committed', ', '.join(f.split('_')[1] for f in files))


print('\n[wiring]')
assert 'resolve_outro_for_render(\n            user_id, data.get(\'outro_asset_id\'))' in APP
ok('process_brands calls the resolver')
route = APP[APP.index("def brandr_outro_preference():"):]
route = route[:route.index('\n@app.route')]
assert "'EXPLORER_LOCKED'" in route and '403' in route
ok('the toggle refuses Explorer', '403, with a reason')
assert 'set_keep_brandr_outro(user_id, enabled)' in route
ok('the write goes through database.py', 'not an undefined _retry_write')
assert 'def set_keep_brandr_outro' in DB and '_retry_write' in DB
ok('and that helper uses the retry discipline')

print('\n%d assertions passed.' % PASS)
