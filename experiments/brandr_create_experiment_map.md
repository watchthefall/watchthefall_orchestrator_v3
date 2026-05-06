# Brandr Create / Composer Experiment Map

This is an isolated experiment artifact, not wired into the stable app.

Prototype file:

- [experiments/brandr_create_job_builder_prototype.html](C:/Users/PC/Desktop/wtf_brandr_app/watchthefall_orchestrator_v3/experiments/brandr_create_job_builder_prototype.html)

## What this prototype is trying to answer

1. How Create should work as a job builder
2. How brand defaults and per-video overrides connect
3. How preview should match output
4. How multi-video + multi-brand + multi-format output should be represented
5. How intro/outro/template features can fit without another rebuild

## Proposed model

Durable unit:

- `output matrix row = source x brand x format`

Layers:

- source item
- brand defaults
- scoped override
- format preset
- optional intro/outro/template pack

Current prototype state:

- `jobItems[]`
  - one item per video input
  - carries selected brands, selected formats, template slot, and `overridesByBrand`
- active scope
  - `activeVideoId`
  - `activeBrandId`
  - `activeFormatKey`

## Existing code reused as reference

Frontend:

- [portal/templates/clean_dashboard.html](C:/Users/PC/Desktop/wtf_brandr_app/watchthefall_orchestrator_v3/portal/templates/clean_dashboard.html:1131)
  - source intake tabs
- [portal/templates/clean_dashboard.html](C:/Users/PC/Desktop/wtf_brandr_app/watchthefall_orchestrator_v3/portal/templates/clean_dashboard.html:1246)
  - brand selection
- [portal/templates/clean_dashboard.html](C:/Users/PC/Desktop/wtf_brandr_app/watchthefall_orchestrator_v3/portal/templates/clean_dashboard.html:1987)
  - preview canvas
- [portal/templates/clean_dashboard.html](C:/Users/PC/Desktop/wtf_brandr_app/watchthefall_orchestrator_v3/portal/templates/clean_dashboard.html:1549)
  - `jobItems` staging model
- [portal/templates/clean_dashboard.html](C:/Users/PC/Desktop/wtf_brandr_app/watchthefall_orchestrator_v3/portal/templates/clean_dashboard.html:1604)
  - `captureCurrentSliders()`
- [portal/templates/clean_dashboard.html](C:/Users/PC/Desktop/wtf_brandr_app/watchthefall_orchestrator_v3/portal/templates/clean_dashboard.html:1655)
  - `getBrandDefaults()`

Backend routes:

- [portal/app.py](C:/Users/PC/Desktop/wtf_brandr_app/watchthefall_orchestrator_v3/portal/app.py:1146)
  - `/api/videos/process_brands`
- [portal/app.py](C:/Users/PC/Desktop/wtf_brandr_app/watchthefall_orchestrator_v3/portal/app.py:1099)
  - `/api/videos/output-contract`
- [portal/app.py](C:/Users/PC/Desktop/wtf_brandr_app/watchthefall_orchestrator_v3/portal/app.py:2022)
  - `/api/preview/extract-frame`

## Important current limitation

Today the renderer still behaves like:

- one `source_path` per request
- one or more brands per request
- no true multi-format rendering variant support yet

So the safest near-term implementation path is:

- frontend builds a richer job matrix
- processor flattens that matrix into repeated backend calls
- non-9:16 formats stay preview/planning concepts until backend variant rendering is added

Sequential processing behavior in the prototype:

- walk `jobItems` in order
- for each video:
  - if all selected brands use defaults, send one request with `brand_ids: [...]`
  - if a brand has scoped overrides, fan out to a single-brand request for that brand
- aggregate the resulting responses back onto the owning `jobItem`

## Why no DB/schema change yet

This experiment does not require persistence changes yet because:

- current queue state already lives client-side in `jobItems`
- we can validate UX and state shape before deciding what deserves long-term storage

Likely later persistence candidates:

- saved packs
- reusable intro/outro bundles
- per-format default sets

## Risks that remain

1. `clean_dashboard.html` currently mixes legacy globals and new `jobItems` state.
2. Multi-format is not output-real yet.
3. Download/result management needs a better tray model for batch jobs.
4. A real integration pass should likely split Create UI state out of the monolithic template.
