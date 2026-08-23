"""Offline assertions for the download-name sanitiser (no Flask, no DB, no network).

Run from the repo root:  python <this file>
"""
import io
import unicodedata

BS = chr(92)  # backslash, kept out of literals to avoid escaping noise

src = io.open('portal/app.py', encoding='utf-8').read()
start = src.index('_FS_ILLEGAL =')
end = src.index('def _friendly_download_name')
ns = {'unicodedata': unicodedata}
exec(src[start:end], ns)
san = ns['_sanitise_download_stem']

PASS = 0


def check(label, got, want):
    global PASS
    assert got == want, 'FAIL %s: got %r, want %r' % (label, got, want)
    PASS += 1
    print('  ok  %-40s -> %r' % (label, got))


print('\n[ugly names from review]')
check('path chars + quotes + question', san('Police / Van: "Incident"?', 'fb'), 'Van-Incident')
check('em dash + ampersand', san('Cat & Dog - August 23', 'fb'), 'Cat-&-Dog---August-23')
check('profanity + emoji', san('What the fuck?! \U0001F62D', 'fb'), 'What-the-fuck!-\U0001F62D')
check('path traversal', san('../../evil', 'fb'), 'evil')
check('percent sign', san('100% genuine', 'fb'), '100%-genuine')

print('\n[degenerate inputs fall back]')
for raw in ['', '   ', '...', '///', BS * 2, '???', None, '.-_']:
    check('%r -> fallback' % (raw,), san(raw, 'FALLBACK'), 'FALLBACK')

print('\n[traversal and separators can never survive]')
check('windows traversal', san('..' + BS + '..' + BS + 'windows' + BS + 'system32', 'fb'), 'system32')
check('absolute path', san('/etc/passwd', 'fb'), 'passwd')
check('trailing slash', san('folder/', 'fb'), 'fb')
check('leading dot (hidden file)', san('.hidden', 'fb'), 'hidden')

print('\n[whitespace + control chars]')
check('collapses runs', san('a    b\t\tc', 'fb'), 'a-b-c')
check('strips control chars', san('bad\x00\x07name', 'fb'), 'badname')
check('trims edges', san('  spaced  ', 'fb'), 'spaced')

print('\n[length is bounded]')
check('capped at 60', len(san('x' * 200, 'fb')), 60)

print('\n[unicode letters preserved]')
check('accents kept', san('Café Münster', 'fb'), 'Café-Münster')

print('\n[no result can contain a separator or reserved char]')
probes = ['a/b', 'a' + BS + 'b', 'a:b', 'a*b', 'a?b', 'a"b', 'a<b', 'a>b', 'a|b']
for probe in probes:
    out = san(probe, 'fb')
    assert '/' not in out, 'forward slash survived: %r' % out
    assert BS not in out, 'backslash survived: %r' % out
    for bad in ':*?"<>|':
        assert bad not in out, 'reserved char %r survived: %r' % (bad, out)
    PASS += 1
print('  ok  all %d separator/reserved probes clean' % len(probes))

print('\n%d assertions passed.' % PASS)
