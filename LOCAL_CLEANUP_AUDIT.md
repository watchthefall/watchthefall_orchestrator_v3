# Local Cleanup Audit

Date: 2026-07-14

This checkout is the real Brandr repository:

`C:\Users\PC\Desktop\wtf_brandr_app\watchthefall_orchestrator_v3`

The parent folder `C:\Users\PC\Desktop\wtf_brandr_app` is a working folder, not a valid Git repository.

## Safe Findings

- `main` matches `origin/main`.
- Render deploys for `d13b99a` are live according to the dashboard screenshots.
- Production is not blocked by the local duplicate files.

## Local Clutter To Remove Or Archive

These are local-only confusion/space risks:

- `watchthefall_orchestrator_v3\watchthefall_orchestrator_v3\` - nested repo, about 578 MB. Do not delete blindly: it has a separate local `origin/main` at `165552f` and uncommitted edits/artifacts. Treat as a messy checkpoint to archive or inspect, not as the current deploy source.
- `..\watchthefall_orchestrator_v3.7z` - archive in the parent folder, about 453 MB.
- `portal\private\db\wtf_studio.db` - local SQLite DB, must not be committed.
- `nul` files and `__pycache__` folders - generated Windows/Python artifacts.

## Guardrail Added

`.gitignore` now ignores:

- local private DB folders and `*.db`
- Python caches
- `.venv`
- archives
- nested duplicate `watchthefall_orchestrator_v3/`
- `nul`

## Recommended Next Step

Do not merge or delete blindly. First choose a source of truth:

1. Keep `C:\Users\PC\Desktop\wtf_brandr_app\watchthefall_orchestrator_v3` as the live deploy repo because it matches the Render deployment screenshots at `d13b99a`.
2. Move the nested `watchthefall_orchestrator_v3\watchthefall_orchestrator_v3\` folder to an archive folder outside the live repo path.
3. Inspect or preserve any useful patch from the nested checkpoint.
4. Delete the old `.7z` archive only after confirming it is not the only backup.
