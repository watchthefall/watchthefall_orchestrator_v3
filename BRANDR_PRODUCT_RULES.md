# BRANDR Product Rules (Team Implementation Style)

**Status:** Draft v1 (implementation-aligned)  
**Audience:** Product, Engineering, Design, QA  
**Purpose:** Define enforceable product rules that keep UX truthful to backend behavior while guiding V2 architecture.

---

## 1) Core Job Formula

Brandr must model all processing workloads using:

> **sources × brands × variants = outputs**

### Enforcement requirements
- This formula must be visible in Create before execution.
- Any change to selected sources/brands/variants must recompute output count immediately.
- If output count exceeds tier limits or safety thresholds, block or partially defer with plain-language explanation.

---

## 2) Current Beta-v1 Truthful Capabilities

This section documents **what is actually supported today** and what UI is allowed to imply.

### 2.1 Media Intake
- Supported:
  - Single URL import in processing flow
  - `source_path` processing for already-downloaded media
  - Multi-link fetch endpoint (up to 5 URLs) for ingestion into library-like storage
- Not yet full matrix processing:
  - Multi-link branding in one matrix job is not implemented.

### 2.2 Processing Model
- Branding is synchronous request-time processing (no async brand queue yet).
- One processing request handles one source input and multiple selected brands sequentially.
- Output files are produced per brand.

### 2.3 Variants
- Vertical/Square/Landscape are a product requirement, but backend rendering is not yet a true per-variant matrix engine.
- Current system should not imply guaranteed multi-variant rendering unless selected path is implemented.

### 2.4 Composition Persistence
- Brand composition fields exist and are persisted at brand level.
- Settings are global defaults per brand (not fully per-variant override mode).

### 2.5 Download + Storage behavior
- Download endpoint supports served output files with authenticated access.
- Storage and retrieval behaviors should be described as implementation-dependent in Beta.

---

## 3) Future V2 Capabilities (Target State)

V2 introduces explicit matrix processing and queue architecture:

### 3.1 Job model
- Accept `sources[]`, `brands[]`, `variants[]` in one request.
- Expand matrix by formula and create a tracked job with deterministic output IDs.

### 3.2 Queue + lifecycle
- Async queue with job states:
  - `queued`
  - `downloading`
  - `rendering`
  - `complete`
  - `partially_blocked`
  - `failed`
- Retry failed items by source/brand/variant scope.

### 3.3 Variant overrides
- Global composition remains default.
- Per-variant overrides are opt-in advanced mode.

### 3.4 Libraries
- Source and output libraries grouped by batch/job and source lineage.
- Bulk export tools (zip, redownload, rerun context).

---

## 4) Tier Definitions

Tiers are defined by **workflow breadth** and **safe throughput**, not fantasy volume claims.

### Explorer
For testing product value with strict limits.

### Creator
For solo creators requiring multi-brand capability and practical batching.

### Studio
For serious creators/teams with broader throughput and premium workflow controls.

### Platinum
For power users/agencies; sold on leverage, smoothness, and breadth (not "unlimited").

### Elite
For negotiated enterprise use, future team controls, and highest operational ceilings.

---

## 5) Limits Per Tier

> These are **product contract limits**. Platform safety may reduce effective execution.

| Tier | fetches/day | Instagram/hour | max links/batch | max brands/job | max variants/job | max outputs/job | max brand configs |
|---|---:|---:|---:|---:|---:|---:|---:|
| Explorer | 20–25 | 3 | 1 | 1 | 1 | 1 | 1–3 |
| Creator | 75–100 | 8–12 | 5 | 5–8 | 3 | 20 | 25–50 |
| Studio | 150–200 | 12–15 | 10 | 20 | 3 | 60 | 100 |
| Platinum | 300+ practical ceiling | safety-capped | 20 | 25–50 | 3 | 100–150 | 250 |
| Elite | negotiated | safety-capped | up to 50 | negotiated | 3+ (as agreed) | 300+ (as agreed) | negotiated |

### Implementation notes
- "max outputs/job" must be enforced as first-class logic, not inferred only from other limits.
- Tier messaging must show blocked reason and next action (reduce selection / upgrade / retry later).

---

## 6) Platform Safety Rules vs User Entitlement

### Rule
Tier entitlement defines what a user can request. Safety defines what the system can execute safely now.

### Safety precedence
- Safety controls always override tier allowances where needed.
- Example: Instagram safety thresholds may require partial processing/defer despite paid tier capacity.

### Required user messaging
When safety intervenes, return plain-language outcomes:
- how many succeeded now
- how many were deferred/blocked
- why
- what user can do next

---

## 7) Create Flow Structure

Create is the command center and must remain explicit and truthful.

## 7.1 Media
- Single link import
- Multi-link batch intake (Beta: intake first)
- Upload file
- From library
- Recent media quick-pick

## 7.2 Brands
- Single or multi-brand selection
- Persist last-used brand set
- Future: folders/tags

## 7.3 Outputs
- Variant names:
  - Vertical (9:16)
  - Square (1:1)
  - Landscape (16:9)
- User must see selected variants and resulting count.

## 7.4 Adjust
- Step 2: Logo controls
- Step 3: Watermark controls
- Step 4: final composition controls

## 7.5 Queue Summary (mandatory)
Display before processing:
- sources selected
- brands selected
- variants selected
- **computed outputs**
- estimated time/cost
- tier impact and safety warnings

---

## 8) Step 2 / Step 3 / Step 4 Responsibilities

## Step 2 — Logo (identity layer)
- Upload/change logo
- Cleanup/crop/shape (where implemented)
- Opacity, size, rotation
- Safe mobile placement defaults

## Step 3 — Watermark (treatment layer)
- Upload watermark or generate from logo (when enabled)
- Opacity/size/shape controls
- Multi-variant preview context
- Save durable defaults only if persistence exists

## Step 4 — Final Composition (integration layer)
- Logo + watermark visible together
- Main interactive preview as source of truth
- Secondary cards mirror computed state
- Drag/snapping/alignment guides
- "Apply to all variants" default
- "Per-variant override" only in advanced opt-in mode

---

## 9) Rules for Previews vs Actual Outputs

### Truthfulness laws
1. UI must not imply true multi-variant rendering unless backend actually renders selected variants.
2. Preview cards must be labeled either:
   - `Preview only`, or
   - `Selected for output`.
3. Every editable control must either:
   - persist fully end-to-end (`save → reload → process → output`), or
   - be hidden until persistence exists.
4. One main interactive preview by default; advanced multi-variant editing is opt-in.
5. Output count must always be visible before execution.

---

## 10) Known Gaps: Current UI vs Backend Truth

## Gap A — Full matrix processing
- Desired: `sources × brands × variants` batch processing.
- Current: single-source processing path for branding; multi-link primarily intake/fetch.

## Gap B — Variant execution truth
- Desired: explicit per-variant output generation.
- Current: variant UX can outpace backend behavior if not labeled.

## Gap C — Queue architecture
- Desired: async queue with resumable, grouped jobs.
- Current: synchronous branding path; limited async behavior in separate conversion workflows.

## Gap D — Tier/safety surfacing
- Desired: upfront output caps and safety-aware partial execution messaging.
- Current: some limits enforced, but not a full user-facing matrix policy contract.

## Gap E — Per-variant overrides
- Desired: controlled advanced override mode.
- Current: primarily global brand-level composition defaults.

---

## Implementation Priority (recommended)

1. **Rules contract in code + UI**
   - Add output count formula and max outputs/job enforcement.
2. **Beta truthfulness pass**
   - Label preview-only vs real output controls.
3. **Create command-center hardening**
   - Queue summary + tier/safety warnings before start.
4. **V2 architecture planning**
   - Matrix jobs, async queue, grouped output model, per-variant overrides.

---

## Canonical Product Positioning Statement

> **Platinum is optimized for workflow breadth and high practical throughput, not unlimited raw export volume. Platform safety and system protection remain higher-priority than tier entitlement.**
