# Plan — get the YouTube residential-proxy experiment onto GitHub without touching main

## Where things stand

- `main` on GitHub: `f22aba2`. Untouched. This must stay true through every option below.
- `origin/experiment/youtube-residential-proxy` on GitHub: also `f22aba2` (confirmed by the user's GitHub screenshot).
- Local Emergent workspace, branch `experiment/youtube-residential-proxy`: one commit ahead at `0a136bd`. This commit contains the two-file change (`portal/app.py` +25/-7, new file `scripts/simulate_youtube_residential_proxy.py` +270).
- All tests pass locally (592 assertions across 22 simulation suites, including the two invariants that guard against broadening the Meta proxy helper and against removing the legacy `IG_PROXY` YouTube fallback).
- The change is fully reversible. Nothing has reached GitHub. Render has not been triggered.
- The user cannot locate a "Save to GitHub" button in the Emergent UI. Their screenshot shows only **Code · Preview · Publish** in the top-right of the app-builder workspace.

The problem the plan has to solve is small and non-technical: **choose the transport that gets `0a136bd` onto `origin/experiment/youtube-residential-proxy`.** The code is done.

---

## The decision

There are four viable options. Only one needs to be chosen.

### Option 1 — Find the built-in GitHub push in Emergent

The "Save to GitHub" control is not always in the chat row. On current Emergent builds it also appears as any of:

- a GitHub icon in the **top bar** near the avatar / credits pill;
- an item inside the **kebab (⋯) menu** on the workspace tab (`proxy-youtube-exp`);
- an entry under **Profile → Integrations → GitHub** in the account menu;
- the **Publish** button's dropdown (Publish is normally the Render/preview publish, but on some builds it fans out into "Publish preview" + "Push to GitHub").

If any of those exposes a branch-selectable push, use it and select `experiment/youtube-residential-proxy`. Do not select `main`. Do not accept any "also merge to main" toggle.

Cost: zero, if it exists. Risk: low, provided the branch selector is respected.

### Option 2 — Emergent support ticket

Ask Emergent support to either point at the current button location or push the branch on the user's behalf. Explicit instruction: push `experiment/youtube-residential-proxy` only, do not touch `main`, do not trigger Render.

Cost: a support round-trip (hours to a day). Risk: none — support can see the workspace state.

### Option 3 — Download the workspace, push from a local clone (recommended fallback)

Concretely:

1. In Emergent, use the workspace download / export (usually near the same GitHub area, sometimes under the kebab menu) to grab the current tree as a zip. Only two files are needed from it: `portal/app.py` and `scripts/simulate_youtube_residential_proxy.py`.
2. On the user's own machine:
   ```
   git clone git@github.com:watchthefall/watchthefall_orchestrator_v3.git
   cd watchthefall_orchestrator_v3
   git checkout main                                       # f22aba2
   git checkout -b experiment/youtube-residential-proxy    # branch off main
   # overwrite the two files from the Emergent zip
   git add portal/app.py scripts/simulate_youtube_residential_proxy.py
   git commit -m "experiment: route only YouTube yt-dlp through DataImpulse when configured"
   git push -u origin experiment/youtube-residential-proxy
   ```
3. Open a PR on GitHub, `experiment/youtube-residential-proxy` → `main`, but **do not merge**. Review the diff. Merge only when satisfied.

Trade-off worth naming explicitly: the resulting commit is authored by the user, not by Emergent's auto-checkpoint. That is usually a positive (clearer authorship, no Emergent metadata in the production history), but it means the SHA won't be `0a136bd` — it will be whatever the local commit produces. The tree contents are identical; the SHA differs because the commit metadata differs.

Cost: 5–10 minutes at a terminal. Risk: lowest of the four options — `main` cannot be moved by accident because the working branch never has `main` checked out for a push.

### Option 4 — Apply the two-file diff by hand

Same as Option 3 but skipping the zip download: paste the diff already shown in this conversation into a `.patch` file locally, `git apply` it against a fresh clone at `f22aba2`, then commit and push. Useful if the Emergent download control is also missing.

Cost: 5 minutes plus one extra step vs. Option 3. Risk: identical to Option 3.

---

## Guardrails that apply to every option

1. `main` must end the exercise at `f22aba2`. Verify on GitHub after any push by loading the branch dropdown and confirming `main`'s tip.
2. Render's deploy trigger should be checked before merging anything to `main`. In the Render dashboard for the Brandr web service, "Branch" should read `main` and "Auto-Deploy" should be on **for that branch only**. If it is set to deploy every branch, a push to `experiment/*` would deploy — which is not wanted. This is a 10-second check on the Render side, not something Emergent can guarantee.
3. The PR that will eventually merge into `main` must show **exactly two files changed**: `portal/app.py` (+25/-7) and `scripts/simulate_youtube_residential_proxy.py` (+270, new file). Anything else on the diff is a red flag and should stop the merge.
4. DataImpulse credentials (`DATAIMPULSE_USERNAME`, `DATAIMPULSE_PASSWORD`, optional `DATAIMPULSE_COUNTRY`) are already configured on Render for the Meta path. No new env var is introduced. If they were somehow unset before this ships, the code degrades to the pre-existing `IG_PROXY` behaviour and then to direct — no crash, no surprise.

---

## Assumptions made without asking

- The user has push access to `origin` for `watchthefall/watchthefall_orchestrator_v3` on GitHub. (Otherwise none of options 1, 3, or 4 work and it becomes option 2 by default.)
- The user has a working local git + ssh setup for GitHub. (If not, https + a personal access token works identically.)
- Render's auto-deploy is scoped to `main`. This is the standard Render configuration and matches how the earlier YouTube-cookie-pool ship reached production. If it turns out to be branch-agnostic, guardrail 2 catches it before any harm.
- No further code changes are wanted before pushing. The suite is green; the diff is the diff already reviewed in-thread.

---

## Open question for the user

Which option do you want to take?

- **Option 1** if you can find the GitHub control in the Emergent UI on one more pass (kebab menu, top bar, or a fly-out on Publish).
- **Option 2** if you'd rather have Emergent support handle the push.
- **Option 3** (recommended) if you'd like to keep the whole thing on your own machine and end up with a clean, user-authored commit on GitHub.
- **Option 4** if the Emergent download is also unavailable — same outcome as 3, one extra step.

Approve one and the build side of this is finished; the rest is reviewing on GitHub and, later, opening the PR when you're ready.
