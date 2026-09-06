# How to apply this patch

The file `youtube-residential-proxy.patch` in this folder is a unified diff
against production commit **f22aba2** on `main`. It reproduces the exact tree
Emergent's auto-checkpoint `0a136bd` produced — the same code that passed all
592 test assertions in the workspace.

Two files change: `portal/app.py` (+25 / −7) and a new file
`scripts/simulate_youtube_residential_proxy.py` (+270). Nothing else.

---

## Steps (from your own machine, ~5 minutes)

```bash
# 1. fresh clone (or use an existing one, but be on a clean tree)
git clone git@github.com:watchthefall/watchthefall_orchestrator_v3.git
cd watchthefall_orchestrator_v3

# 2. anchor to production
git checkout main
git rev-parse HEAD    # MUST print f22aba2...

# 3. branch off main
git checkout -b experiment/youtube-residential-proxy

# 4. drop the patch file into the repo root (drag/drop, scp, curl,
#    or paste-into-editor — whatever's easiest)

# 5. apply it
git apply --check youtube-residential-proxy.patch    # dry run, must exit 0
git apply youtube-residential-proxy.patch

# 6. verify the shape
git status
git diff --stat main
# expect:
#   portal/app.py                                    | 32 +++++++++++++++++++-------
#   scripts/simulate_youtube_residential_proxy.py    | 270 +++++++++++++++++++
#   2 files changed, 295 insertions(+), 7 deletions(-)

# 7. quick sanity test (no network, ~1 second each)
python scripts/simulate_youtube_residential_proxy.py
python scripts/simulate_youtube_cookie_rotation.py
python scripts/simulate_proxy_service.py
# all three should end with "N assertions passed."

# 8. commit and push
git add portal/app.py scripts/simulate_youtube_residential_proxy.py
git commit -m "experiment: route only YouTube yt-dlp through DataImpulse when configured

When DataImpulse is configured (same DATAIMPULSE_USERNAME/PASSWORD
credentials already in use for Instagram/Threads), route YouTube
yt-dlp fetches through the residential proxy via
proxy_service.get_proxy_url(). Falls through to the pre-existing
IG_PROXY branch, then direct, when DataImpulse is not configured.

Meta helper (get_meta_proxy) is NOT broadened; YouTube reuses the
lower-level get_proxy_url() so the two providers stay independent.
YouTube cookie pool and YT_PLAYER_CLIENTS are untouched.

Adds scripts/simulate_youtube_residential_proxy.py with 20 offline
assertions covering wiring, precedence in every config combination,
scope isolation, credential redaction, and transport-error
classification. Full suite: 592 assertions across 22 simulations,
all green."

git push -u origin experiment/youtube-residential-proxy
```

---

## After push

1. On GitHub, load
   `github.com/watchthefall/watchthefall_orchestrator_v3/compare/main...experiment/youtube-residential-proxy`
2. Confirm **exactly two files** changed, `+295 / −7`. Anything else is a red
   flag — stop.
3. Confirm on GitHub that `main` is **still `f22aba2`**.
4. Optionally open a **Draft PR** so review comments have a home. Do not merge.

---

## If `git apply --check` fails

That would mean the local `main` isn't at `f22aba2` (the patch's context lines
wouldn't line up). Re-run `git rev-parse HEAD` on `main` and make sure it prints
`f22aba2` before applying. Do not force the apply on a different base — send me
the mismatch instead and I'll produce a rebased patch.

---

## Notes

- The patch is a plain unified diff, not a `git format-patch` mailbox, so
  `git apply` is the right tool (not `git am`). This is deliberate — you get to
  author the commit yourself, so the commit ends up under your name/email
  rather than Emergent's auto-checkpoint metadata.
- No credentials of any kind are in the patch. The DataImpulse env vars are
  read at runtime from Render's environment settings, which the patch does not
  touch.
- If you have concerns about running `python scripts/simulate_*.py` on your own
  machine, they're all pure-stdlib and read-only against the repo — no network,
  no DB, no yt-dlp calls. Safe to skip if you'd rather just push and rely on
  the Emergent-side run that already passed.
