"""Brand limits and the founding window -- the two customer-visible commercials.

Both were drift, and both were drift of the dangerous kind: the numbers we
ENFORCE had come apart from the numbers we ADVERTISE.

  * max_brand_configs (TIER_CONFIG) is what the server enforces and what
    profile.html shows you about your own account.
  * max_brand_configs_total (TIER_FEATURES) is what the UPGRADE MODAL shows a
    prospect -- i.e. what we are selling.

Nothing kept them equal, so they weren't. The invariant below is the real
deliverable here; the specific numbers are just today's values.

The founding model was worse than stale: 'max_slots_per_tier' capped how many
early customers we were ALLOWED to win, and 'lock_months' expired the founding
price after a year -- re-pricing our earliest supporters upward. It also wrote
that expiry into users.bonus_tier_until, a SHARED column owned by the referral
system.

Runs the REAL functions, AST-extracted from portal/config.py and executed
without importing the module (importing it creates storage directories):

    python scripts/simulate_tier_commercials.py
"""
import ast
import io
import os
from datetime import date

CFG_SRC = io.open(os.path.join('portal', 'config.py'), encoding='utf-8').read()
APP = io.open(os.path.join('portal', 'app.py'), encoding='utf-8').read()
DB = io.open(os.path.join('portal', 'database.py'), encoding='utf-8').read()
MODAL = io.open(os.path.join('portal', 'templates', '_upgrade_modal.html'),
                encoding='utf-8').read()

PASS = 0


def ok(label, extra=''):
    global PASS
    PASS += 1
    print('  ok  %-56s %s' % (label, extra))


# --- execute just what we need from config.py --------------------------------
WANT_ASSIGN = {'TIER_CONFIG', 'TIER_FEATURES', 'FOUNDING_WINDOW_END',
               'FOUNDING_MEMBER_CONFIG', 'SPECIAL_STATUSES'}
WANT_FUNC = {'founding_window_open', 'founding_days_remaining', 'price_for_tier'}

ns = {'date': date}
for node in ast.parse(CFG_SRC).body:
    if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in WANT_ASSIGN for t in node.targets):
        exec(ast.get_source_segment(CFG_SRC, node), ns)
    elif isinstance(node, ast.FunctionDef) and node.name in WANT_FUNC:
        exec(ast.get_source_segment(CFG_SRC, node), ns)

TIER_CONFIG = ns['TIER_CONFIG']
TIER_FEATURES = ns['TIER_FEATURES']
price_for_tier = ns['price_for_tier']
window_open = ns['founding_window_open']
days_left = ns['founding_days_remaining']
WINDOW_END = ns['FOUNDING_WINDOW_END']


print('\n[brand limits match the documented model]')
EXPECTED = {'Explorer': 3, 'Creator': 7, 'Studio': 10, 'Platinum': 50}
for tier, want in EXPECTED.items():
    got = TIER_CONFIG[tier]['max_brand_configs']
    assert got == want, '%s enforces %r, docs say %r' % (tier, got, want)
ok('enforced limits are 3 / 7 / 10 / 50', 'was 1 / 5 / unlimited / unlimited')
assert TIER_CONFIG['Elite']['max_brand_configs'] == -1
ok('Elite stays unlimited', 'invitation-only, not a sold tier')

# THE INVARIANT. What we enforce and what we advertise must be the same number
# for every tier, forever -- not just for the four we happened to check.
for tier in TIER_CONFIG:
    enforced = TIER_CONFIG[tier]['max_brand_configs']
    advertised = TIER_FEATURES[tier]['max_brand_configs_total']
    assert enforced == advertised, (
        '%s: enforces %r but the upgrade modal advertises %r'
        % (tier, enforced, advertised))
ok('enforced == advertised for EVERY tier', 'the guard against re-drifting')
assert ns['SPECIAL_STATUSES']['beta_tester']['overrides']['max_brand_configs'] == -1
ok('beta_tester override still unlimited', 'more generous, so it still wins')


print('\n[the founding window is a deadline, not a quota]')
assert WINDOW_END == date(2026, 12, 31), WINDOW_END
ok('window ends 31 December 2026')
assert 'max_slots_per_tier' not in CFG_SRC and 'lock_months' not in CFG_SRC
ok('no slot quota, no 12-month lock', 'both models removed')
assert window_open(date(2026, 12, 31)) is True
ok('open on the final day', 'inclusive')
assert window_open(date(2027, 1, 1)) is False
ok('closed the next morning')
assert days_left(date(2026, 12, 1)) == 30
assert days_left(date(2027, 6, 1)) == 0
ok('days remaining never goes negative')


print('\n[pricing: one function, so nothing can disagree]')
DURING = date(2026, 10, 6)     # launch day -- canonical date
AFTER = date(2027, 3, 1)

for tier in ('Creator', 'Studio', 'Platinum'):
    full = TIER_CONFIG[tier]['price']
    founding = TIER_CONFIG[tier]['founding_price']
    assert founding < full

    got = price_for_tier(tier, founding_status=False, today=DURING)
    assert got == founding, (tier, got, founding)

    got = price_for_tier(tier, founding_status=False, today=AFTER)
    assert got == full, (tier, got, full)
ok('window open -> everyone pays the founding rate')
ok('window closed -> a new customer pays full price')

# The subtle one: a founder who moves tier keeps founder pricing on the NEW tier.
got = price_for_tier('Studio', founding_status=True, subscription_active=True,
                     today=AFTER)
assert got == TIER_CONFIG['Studio']['founding_price'], got
ok('founder + continuously subscribed + tier change',
   'founder price on the NEW tier')

got = price_for_tier('Creator', founding_status=True, subscription_active=False,
                     today=AFTER)
assert got == TIER_CONFIG['Creator']['price'], got
ok('founder who LAPSED pays full price', 'status permanent, price is continuity')

for tier in ('Explorer', 'Elite'):
    got = price_for_tier(tier, founding_status=True, subscription_active=True,
                         today=DURING)
    assert got == TIER_CONFIG[tier]['price'], (tier, got)
ok('Explorer and Elite are not founding-eligible', 'free and invite-only')


print('\n[the expiry stamp is gone, and stopped clobbering a shared column]')
claim = DB[DB.index('def claim_founding_slot('):DB.index('def revoke_founding_status(')]
assert 'bonus_tier_until' not in claim.split('"""')[2]
ok('claim_founding_slot no longer writes bonus_tier_until',
   'referral rewards stack there')
assert 'expires_at' not in claim
ok('and computes no expiry at all', 'founder status is permanent')
revoke = DB[DB.index('def revoke_founding_status('):]
revoke = revoke[:revoke.index(chr(10) + 'def ')]
assert 'bonus_tier_until' not in revoke
ok('revoke no longer NULLs it either')


print('\n[callers stopped counting slots]')
for token in ('max_slots_per_tier', 'founding_slots_remaining', 'slots_remaining'):
    assert token not in APP, token
ok('app.py has no quota arithmetic left')
assert 'founding_window_open()' in APP
ok('the upgrade link gates on the window')
assert 'or is_founder' in APP
ok('...and still offers the founder rate to an existing founder')
assert 'slots' not in MODAL
ok('the upgrade modal advertises a deadline, not scarcity')
assert 'even if you cancel and rejoin' not in MODAL
ok('and no longer promises the price back after cancelling',
   'that contradicted the continuity rule')

print(chr(10) + '[what the SALES PAGE actually renders]')
# Comparing config to config is not enough: Studio and Platinum advertised
# 'Unlimited brand configs' as literal TEXT, so the number could change and the
# upgrade modal would go on saying whatever it had been typed to say. The only
# way to catch that class of drift is to render the page and read it.
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader(os.path.join('portal', 'templates')))
env.globals['url_for'] = lambda *a, **k: '#'
html = env.get_template('_upgrade_modal.html').render(
    tier='Explorer', all_tier_features=TIER_FEATURES, all_tier_config=TIER_CONFIG,
    founding_open=True, founding_days_left=90, founding_window_end=WINDOW_END,
    founding_payment_links={})
rendered = ' '.join(html.split())

for tier, want in EXPECTED.items():
    needle = '%d brand configs' % want
    assert needle in rendered, 'the upgrade modal never says %r' % needle
ok('every tier advertises its real limit', '3 / 7 / 10 / 50 all rendered')
assert 'Unlimited brand configs' not in rendered
ok('no tier still claims unlimited', 'Studio and Platinum were hardcoded text')
assert '3 brand config ' not in rendered
ok('plural agrees with the number', 'it read 3 brand config before')
assert WINDOW_END.strftime('%d %b %Y') in rendered
ok('the founding deadline is on the page', 'replacing N slots left')

print('\n%d assertions passed.' % PASS)
