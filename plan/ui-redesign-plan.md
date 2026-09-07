# Brandr UI Redesign — Plan

**Status:** Plan only. No code changes proposed in this document. No commits, no
deploys, no branch pushes.

**Baseline:** `main` @ `f22aba2` (production). This plan is authored on the
Emergent sandbox at commit `0a136bd` (the YouTube residential proxy experiment),
but it is independent of that experiment and would land on a fresh branch off
`main`.

**Author:** e1 agent session, informed by user-supplied concept mockups (Studio
amber / Creator purple / Explorer green / Platinum silver / Founding Member
gold) and by the reframed spec ("Brandr is a composable production formula, not
a watermarking tool").

---

## 0. TL;DR

The redesign is **not** "prettier brand tiles". It is **"make the composable
production formula legible"**. The user is constructing a matrix
`Source × Format × Brand × [Optional Processing] × [Optional Destination]` and
the UI's job is to make that multiplication visible, editable, and
extensible — today for three dimensions, tomorrow for six.

Good news: the codebase's **theme layer is already correctly architected**.
`portal/static/css/styles.css` already defines complete `data-tier` token
bundles for all five tiers plus a `Founding` overlay. The "developer-y" feel of
the live app is a **usage problem, not an architecture problem**. Templates use
`var(--accent)` but treat it as a colour swap; they don't do the layering,
depth, halo, and motion work that makes the concept mockups feel like a
product.

Bad news: the current Create flow is **hard-coded around today's specific
dimensions** (Video → Format → Brand-with-flag-tile) in the templates, in the
brand grid, and in the "Ready to brand" summary strip. Any redesign that
carries those hard-codes forward will architecturally box Brandr into today's
formula and make adding Transcription / Destination / AI-Processing far more
expensive than it should be.

The plan below is 6 slices, each a separate branch off `main`, each rollback-
able independently. Slice 1 (composable dimensions data model in the frontend)
is a prerequisite for the visual work; without it we'll be repainting a wall
we're about to knock down.

---

## 1. Core reframing — the production formula

### 1.1 The formula (from the reframed spec)

```
SOURCE
  ×
FORMAT
  ×
BRAND
  ×
[OPTIONAL PROCESSING]     ← transcription, AI edits, colour-grade, …
  ×
[OPTIONAL DESTINATION]    ← Instagram, TikTok, YouTube, X, Facebook, …
```

- Every axis is **plural** (`N sources × M formats × K brands × …`).
- Every axis is **optional** except Source and Brand (Format defaults to a
  configured set; missing dimensions render as "unused").
- The **RenderItem** is one concrete point in the matrix.
- **Cost = product of the enabled axes' cardinalities.** The
  Outputs / Formats / Credits summary card is literally showing the user the
  size of the tensor they've just constructed.

### 1.2 What the RenderItem already gets right

From `portal/app.py` (grep of `source_edit` and `render_source_edit`):

```
RenderItem {
    video,           ← source
    brand_id,        ← brand (config bundle: logo, wm, positioning, styling, …)
    format,          ← 9:16 | 1:1 | 16:9
    source_edit,     ← per-format reframe/crop overrides (already generic)
}
```

Two things this shape already gets right that the plan must preserve:

1. **`brand_id` is opaque.** It's a foreign key, not a hard-coded enum of
   country brands. `portal/brand_loader.py::get_available_brands()` loads from
   the DB with a JSON fallback, and every brand's assets/options are
   per-brand-config — not per-country. Nothing at the RenderItem level knows
   or cares that today's brand catalogue is geographic.
2. **`source_edit` is keyed by `(user_id, source_filename, output_format)`.**
   That's already a dimension-agnostic override mechanism. Adding a
   `destination` or `processing` slot follows the same shape: keyed by
   RenderItem coordinates, decoupled from any particular set of values.

### 1.3 What has to become generic

The UI, however, currently assumes:

- Brand tiles = square WTF-logo cards (see the live-app screenshot user
  supplied — every tile is a near-identical dark square).
- Brand grid is flat and enumerated (no search, no filter, no chips).
- The "Ready to brand" strip renders exactly the three-dimension product
  (`videos × brands × formats`) inline as prose ("2 outputs · 2 credits").
- The Studio review step iterates `(brand, format)` pairs assuming exactly
  those two dimensions exist.

Every one of those is a **future-hostile choice** even if it looks fine for
today's three-dimension world. A `Destination` axis added later would either
(a) require the review step to be re-scaffolded, or (b) get shoehorned into
Brand config, which is what we're explicitly avoiding.

---

## 2. What's already right (do NOT touch)

**Do NOT change these under the guise of "redesign". They're load-bearing.**

- `portal/proxy_service.py` and the YouTube cookie pool — recent, tested,
  scoped correctly.
- `portal/app.py::fetch_videos_from_urls` and `process_brands` — the fetch and
  render pipelines. Redesign is UI-only.
- `RenderItem` shape and `source_edit` mechanism — already dimension-agnostic
  where it matters.
- `TIER_CONFIG` in `portal/config.py` — the tier system's data model
  (Explorer / Creator / Studio / Platinum / Elite + Founding overlay) is
  already correct, including per-tier `color`, `accent`, `badge_image`.
- `portal/static/css/styles.css` lines 1–~90 — the tier token bundles. Every
  tier already has `--accent`, `--accent-hover`, `--accent-soft`, `--accent-
  muted`, `--accent-glow`, `--accent-border`, `--accent-text`, `--accent-rgb`,
  `--tier-logo`, `--tier-icon`. This is *exactly* the right shape; we extend
  it in Slice 2, we do not replace it.
- The `data-tier` attribute mechanism on `<html>` — a founder gets
  `data-tier="Founding"` regardless of their base tier, which correctly
  models "founding wins" from the user's spec.
- Brand data model (DB-backed with JSON fallback) — assets and options are
  per-brand, not per-country.

---

## 3. Anti-boxing rules (hand these to every future agent)

These are non-negotiable, in priority order. Any implementation slice that
violates one of these should be rejected in review before code is read.

1. **No new hard-coded brand identifiers.** Do not enumerate `ScotlandWTF`,
   `EnglandWTF`, `BritainWTF`, etc. in any new frontend code. If a component
   needs to render brand-specific content, it takes a brand config object and
   renders from that. Test: grep the diff for any WTF-suffix string literal —
   if it exists in new code, it's wrong.

2. **No geographic assumptions in the brand tile component.** The tile takes:
   `{ id, display_name, logo_url, watermark_url, accent_color?, badge?, … }`.
   It does NOT take a `country_code` or a `flag_asset`. If a customer's brand
   catalogue is *not* geographic (which is the whole point of Brandr being
   extensible), the tile still works. Countries-with-flags is *one* dataset
   the tile can render; it is not the tile's schema.

3. **The Create flow is dimension-driven, not step-driven.** The current
   flow is `Downloadr → Studio → Results` (three literal steps). The
   redesigned flow should be conceptually `configure the matrix → review each
   cell → export`. When a new dimension (Destination, Processing) is added,
   it becomes a new configurable axis in step 1 — NOT a new hard-coded step.

4. **The "Ready to brand" summary is a tensor recap, not a fixed-column
   card.** Today it shows `videos × brands × formats`. Its component takes a
   list of axes with cardinalities and renders the multiplication. Add a
   `destinations` axis and the card grows a column, no rewrite required.

5. **Brand config is a bundle.** A Brand owns logo, watermark, positioning,
   styling, text rules, intro/outro, and future config fields. Adding a
   `caption_style` or `outro_variant` field is a Brand-config concern, not a
   new top-level dimension. Do not promote brand-internal config into new
   top-level formula axes.

6. **Tier theming is done via `data-tier`, not via inline styles or JS
   overrides.** Every visual accent uses `var(--accent)` (or the extended
   tokens defined in Slice 2). If a component needs a colour that isn't in
   the token set, the token set is missing a name — add it; do not hard-code
   a hex.

7. **No text like "watermarking tool" anywhere user-facing.** The product
   frame is "content production engine". Copy work is Slice 6.

---

## 4. What's actually broken in the live app

Grounded observations from the user's supplied live-app screenshots
(`brandr.online/portal/brand`) contrasted with the concept mockups.

### 4.1 Brand selection (the biggest single UX loss today)

| Aspect | Live app now | Concept mockups | Delta severity |
|---|---|---|---|
| Tile identity | Near-identical WTF-square logos in dark tiles | Flags on circular chips (in the concept dataset; must be **general** in code) | High — scan time |
| Search | None | Search input above grid | High — required past ~10 brands |
| Filters | None | Filter dropdown ("region", "type", user-defined) | Medium |
| Selected recap | Outlined tile only | Removable chips row below grid | High — one-tap deselect |
| Grouping | Flat alpha | Grouped in mockup, but implementation must be brand-metadata-driven, not country-hard-coded | Medium |
| Empty/loading/error states | Not visible in live app | Explicit in concept mockups (empty = "Add your first brand" CTA) | Medium |

**Anti-boxing note for Slice 4:** the tile does NOT take a `flag`. It takes a
`logo_url`. If a customer's brand catalogue is football teams or podcasts or
skincare SKUs, the same tile renders those logos. Flags happen to be what
the WTF brand catalogue's logos *are*. That's a data fact, not a component
contract.

### 4.2 Visual hierarchy / "developer-y" feel

Every current-app screenshot has:
- Flat panels of the same shade of dark on the same background.
- Uniform 1px hairline borders everywhere.
- Buttons that read as HTML controls (yellow rectangles with black text).
- No layering, no glow, no depth. Nothing draws the eye to the CTA.
- Hero blocks (`CREATE` heading) sized like body copy.

The concept mockups introduce (in *token-level*, not one-off, form):
- **Elevation layers** — `--bg-elevated-1 / -2 / -3` for nested cards.
- **Accent glow** — `--accent-glow` (which already exists as a token!) used
  as `box-shadow`, halos, and border-radius fills on primary CTAs.
- **Hero mark treatment** — the giant "B" mark on the right of the CREATE
  hero, themed per-tier.
- **Section headers** — icon + eyebrow + title + subtitle pattern
  (`CONTENT READY / Your video is ready to brand.`), used repeatedly.
- **Live badges** — small pill tokens (`LIVE`, `PREVIEWING`, `EDITED`) on
  format tiles and brand tiles to communicate state at a glance.

None of this needs new tokens *at the accent layer* — that's done. It needs
**structure tokens** (elevation, radius, spacing, motion) and **component
patterns** built on top of the accent tokens.

### 4.3 The "Ready to brand" panel (monetisation surface)

This is the panel that shows the size of the tensor the user has constructed
and the cost. In the live app it's a two-line text strip. In the concept
mockups it's the primary CTA panel with:
- Five stat blocks (Videos / Outputs / Formats / Credits required / Credits
  available).
- A red-outlined `NOT ENOUGH CREDITS` inline warning state with `Get more`
  CTA.
- A themed hero `START REVIEW` button with the tier's mark.

**This is the entire upsell surface of the product.** It is currently
invisible. Slice 5 is where we monetise the redesign.

### 4.4 Studio review

Both the live app and the concept mockups show the Studio review as an
iteration over `(brand, format)` pairs with reframe controls. That's fine
today. It becomes hostile the moment we add a `Destination` axis — because
the same `(video, brand, format)` might need to render differently for
Instagram vs YouTube. Slice 6 addresses this **at the level of the
navigation model** (breadcrumb: `Video 1 → BrandName → 9:16 → Instagram`),
not the visual model.

---

## 5. Concept → current delta matrix

Ordered by ratio of user-visible value to implementation cost.

| # | Change | Value | Cost | Files (est.) | Slice |
|---|---|---|---|---|---|
| 1 | Extend CSS tokens with elevation/radius/spacing/motion families | High (unblocks everything else) | Low | `portal/static/css/styles.css` | 2 |
| 2 | Themed hero mark component + tier logo swap | High (perceived-quality jump) | Low | `_nav.html`, one new partial | 3 |
| 3 | Brand tile component v2 (logo-forward, chip-shaped, state badges) | High | Med | `brands.html`, `dashboard.html` or `clean_dashboard.html`, one new partial | 4 |
| 4 | Brand grid search + filters + selected chips row | High | Med | same as #3 + a small JS module | 4 |
| 5 | "Ready to brand" tensor summary + insufficient-credits state | High (revenue surface) | Low-Med | `dashboard.html`, `_upgrade_modal.html` | 5 |
| 6 | Sources card with multi-URL paste + per-source preview | Med | Med | `downloader.html` or `dashboard.html` | 5 |
| 7 | Copy refresh (product frame: production engine, not watermarker) | Med | Low | scattered templates | 6 |
| 8 | Review flow breadcrumb (dimension-driven) | Med (future-proofing) | Med | `dashboard.html` review step | 6 |

Intentionally **not** on this list:
- Rewriting `dashboard.html` from scratch. Half a dozen touches beat one
  rewrite for reviewability, revertability, and confidence.
- Introducing a JS framework (React, Vue). Brandr is Flask + server-rendered
  Jinja. Slot in vanilla-JS enhancement where needed; do not port.
- Any change to the Python side.

---

## 6. Ordered implementation slices

Each slice = one branch off `main`, one PR, independently rollback-able. Same
discipline as `experiment/youtube-residential-proxy`. Every slice ends with a
manual QA checklist and (where applicable) a curl or offline assertion script
in `scripts/simulate_*.py` style.

### Slice 1 — Composable-dimensions frontend data model *(prerequisite)*

**Branch:** `experiment/ui-dimensions-model`

**Goal:** Give the Create flow a client-side representation of the production
matrix, so subsequent slices are rendering *from* that model rather than
weaving new hard-codes.

**Change shape:**
- Add a small module `portal/static/js/production_formula.js` (new file,
  ~150 lines vanilla JS) exporting:
  - `class ProductionFormula { sources[], formats[], brands[], processing[]=[], destinations[]=[] }`
  - `.cardinality()` → `{ videos, outputs, per_axis }`
  - `.iterCells()` → generator yielding `{ source, brand, format, destination?, processing? }`
  - `.serialize() / deserialize()` for form-submit compatibility with today's
    `process_brands` endpoint.
- The class MUST accept axes it doesn't recognise (forward-compat) — extra
  axes are stored, exposed via `.axes()`, and included in cardinality.
- No template changes yet.

**Files touched:** 1 new (`portal/static/js/production_formula.js`).
**Files NOT touched:** every template, every Python file, every CSS file.

**Testable in isolation:** add `scripts/simulate_production_formula.py` that
loads the JS file's exported spec as JSON (from a companion `.json` fixture
generated by the JS module) and asserts cardinality math for representative
axis combinations, including with a hypothetical `destinations=[…]` axis.

**Rollback:** delete one file.

### Slice 2 — Structure token layer

**Branch:** `experiment/ui-structure-tokens`

**Goal:** Add the token families the concept mockups need, without touching
any component yet.

**Change shape:** Extend `:root` in `portal/static/css/styles.css` with:
- `--bg-base`, `--bg-elevated-1`, `--bg-elevated-2`, `--bg-elevated-3`,
  `--bg-overlay`.
- `--radius-sm`, `--radius-md`, `--radius-lg`, `--radius-hero`, `--radius-
  chip`.
- `--space-1 … --space-8` on a 4px grid.
- `--motion-fast: 120ms cubic-bezier(…)`, `--motion-med: 240ms …`, `--motion-
  slow: 480ms …`.
- `--shadow-elevated-1 … -3`, `--shadow-hero-glow` (references
  `--accent-glow` so it's per-tier automatically).
- `--font-display` and `--font-body`, referencing the currently-used stack
  so nothing shifts unless a downstream component opts in.

**Files touched:** 1 (`portal/static/css/styles.css`, additive only).
**Files NOT touched:** every template, every JS, every Python file.

**Rollback:** revert the single CSS block.

### Slice 3 — Themed hero mark + nav polish

**Branch:** `experiment/ui-hero-mark`

**Goal:** The first slice a user notices. Introduce the animated tier-themed
"B" mark from the mockups, and re-treat `_nav.html` so tier identity reads
at a glance.

**Change shape:**
- New partial `portal/templates/_hero_mark.html` — an inline SVG "B" that
  uses `var(--accent)` and `var(--accent-glow)` for stroke, fill, and
  filter. NOT a `<img>`; SVG so it themes reactively.
- `_nav.html` — tier pill uses `var(--tier-icon)` (already tokenised) with
  the new glow shadow.
- One `<link>` add for a display font (Slice 2 tokens already refer to it).

**Files touched:** `_nav.html`, new `_hero_mark.html`, `styles.css` (only
component-scoped rules).
**Files NOT touched:** any Python, any JS behaviour, brand list, review flow.

**Rollback:** revert three files.

### Slice 4 — Brand tile v2 + grid search/filter/chips

**Branch:** `experiment/ui-brand-grid`

**Goal:** The workflow-improvement slice. Rebuild the brand selection block
per the anti-boxing rules in section 3.

**Change shape:**
- New partial `portal/templates/_brand_tile.html`: takes a brand config,
  renders logo-forward, with state badges (`SELECTED`, `PREVIEWING`,
  `EDITED`, `NEEDS ASSETS`), removable via `×` when in selected-chip form.
- `brands.html` and whichever Create-flow template owns the grid
  (`dashboard.html` / `clean_dashboard.html` — confirm at implementation
  time) use the new partial.
- New JS module `portal/static/js/brand_grid.js` (~120 lines vanilla): text
  search (`display_name`, `tags[]` if present), filter by any brand-config
  metadata field (NOT country — driven by whatever tags/attributes the DB
  exposes), selected-chips row synchronised with the grid.
- Grid takes brand list from the existing endpoint; no backend changes.

**Anti-boxing check:** the tile partial must have zero `WTF`-suffix, zero
`country_code`, zero geographic assumption. Verified by grep in the PR
description.

**Files touched:** 1–2 templates, 1 new partial, 1 new JS module,
`styles.css` (component-scoped).
**Files NOT touched:** Python, brand data model, RenderItem shape.

**Rollback:** revert 4–5 files; grid falls back to the pre-existing partial
via a feature-flag template include.

### Slice 5 — Tensor summary card + insufficient-credits state

**Branch:** `experiment/ui-tensor-summary`

**Goal:** Turn the currently-invisible cost surface into the monetisation
CTA.

**Change shape:**
- New partial `portal/templates/_tensor_summary.html`: takes the
  ProductionFormula instance's cardinality output and renders the five
  stat blocks (Videos / Outputs / Formats / Credits required / Credits
  available). Grows a column automatically for any additional axis
  (destinations, processing, …) — that's the point.
- `NOT ENOUGH CREDITS` state renders inline with a `Get more` button that
  opens `_upgrade_modal.html` (already exists).
- The primary CTA (`START REVIEW`) becomes the tier-themed hero button.
- Backend: no changes. Card reads from the existing usage/credit endpoints.

**Files touched:** 1 new partial, 1–2 templates, `styles.css` (component
scoped).
**Files NOT touched:** anything credit-accounting on the Python side.

**Rollback:** revert 3–4 files.

### Slice 6 — Copy refresh + review breadcrumb

**Branch:** `experiment/ui-copy-and-review`

**Goal:** Reframe copy from "watermarking tool" to "production engine", and
future-proof the review step's navigation model for the coming
destination/processing axes.

**Change shape:**
- Copy edits across `dashboard.html`, `_nav.html`, marketing headings.
  Every string change is one-line, easy to review.
- Review step gets a breadcrumb component `_review_breadcrumb.html` that
  reads from the ProductionFormula cell being reviewed:
  `Video 1 › ScotlandWTF › 9:16` today, `Video 1 › ScotlandWTF › 9:16 ›
  Instagram` when the destination axis lands. Breadcrumb component takes
  a cell dict and renders every non-null axis.

**Files touched:** several templates for copy, 1 new partial for
breadcrumb.
**Files NOT touched:** review-step Python endpoints, source_edit, render
pipeline.

**Rollback:** revert per-file.

---

## 7. Explicit non-goals for this redesign

- Not implementing a Destination axis. This plan makes it cheap to add
  later; adding it is a separate project.
- Not implementing Processing / Transcription. Same.
- Not touching billing / Stripe. Separate project, discussed and deferred.
- Not touching the YouTube residential proxy experiment. Different branch,
  different lifecycle.
- Not migrating to React / Vue / Svelte. Flask + Jinja + vanilla JS stays.
- Not changing the DB schema. Every slice reads from existing endpoints.
- Not adding a marketing landing page. `waitlist.html` and marketing
  surfaces are out of scope; this plan is the *authenticated app*.

---

## 8. Open questions (answer these before Slice 1)

1. **Founding Member vs Elite gold.** The codebase has two gold identities:
   - `Elite` tier — hidden, invitation-only, `#C79619`. A base tier.
   - `Founding` — `data-tier` overlay applied on top of any base tier for
     users who joined in the founding window, `#E8B923`. Not a base tier.

   Your "Founding Member — Early Access — brandr.online" logo image and
   your `BRANDR STUDIO / CREATOR / EXPLORER / PLATINUM` logo images
   together suggest **six** identities, not five. Is the Founding Member
   logo:

   - (a) The overlay skin any founder sees regardless of base tier (my
     current read of the code), *or*
   - (b) A dedicated "Founding Member" tier that should be added to
     `TIER_CONFIG` alongside Elite, *or*
   - (c) Elite renamed to Founding Member for external presentation
     (Elite still exists internally as the invitation-only route)?

   The plan currently assumes (a). If it's (b) or (c), Slice 3's hero
   mark work changes.

2. **Which template owns the Create flow today?** `dashboard.html`,
   `clean_dashboard.html`, or `downloader.html`? All three exist. I'll
   confirm at implementation time (one grep), but if you already know the
   answer, tell me and Slice 3/4/5 file lists get sharper.

3. **Brand metadata for filters.** Slice 4's filter dropdown needs
   metadata to filter *by*. Today's `brand_config.json` has:
   `display_name`, `assets`, `options` — no tags, no category, no
   region. Do you want Slice 4 to (a) introduce a `tags: []` field on
   Brand, back-populated for existing brands from a mapping, or (b) ship
   without filters until a `tags` migration lands, or (c) filter by
   `is_system / is_locked / user_id` (fields that already exist per
   `brand_loader.py`)?

4. **Empty / loading / error states.** The concept mockups only show
   happy-path states. Which of these do you want defined in this plan
   pass, and which are deferred?
   - No brands yet (empty state for new users).
   - Source URL fetch failed (e.g. YouTube bot-gate that even DataImpulse
     can't unblock).
   - Insufficient credits before user has hit `START REVIEW`.
   - Render mid-progress (websocket / polling state — does the app have
     one today?).

5. **Motion preferences.** Do we honour `prefers-reduced-motion` from the
   outset? (Recommended: yes; adds ~10 lines to `styles.css`.)

6. **Live-app dead data.** Screenshots show a `Test` and a `Teat` brand
   in the live brand list with `⚠ Needs logo + watermark` warnings.
   Redesign is a good excuse to add a "needs assets" state to the brand
   tile (already listed in Slice 4). Should Slice 4 also add an admin
   affordance to delete/hide brands with incomplete configs, or leave
   that as-is?

---

## 9. Estimated implementation costs (for future planning; not this session)

**Not a commitment.** Rough ranges assuming an agent operating with the
`experiment/youtube-residential-proxy`-style discipline.

| Slice | Est. LOC (net) | Files touched | Confidence |
|---|---|---|---|
| 1 (dimensions model) | ~200 | 1 new + 1 test | High |
| 2 (structure tokens) | ~60 | 1 CSS block | Very high |
| 3 (hero mark + nav) | ~150 | 3 | High |
| 4 (brand grid v2) | ~400 | 4–5 | Medium |
| 5 (tensor summary) | ~200 | 3–4 | High |
| 6 (copy + breadcrumb) | ~120 | 5–8 | High |

Total: ~1,100 net LOC across ~15–20 files. Every slice reverts to `main`
independently.

---

## 10. What this document is NOT

- Not a design spec. It doesn't specify pixel values, exact typography,
  or exact motion curves. Those belong in a design pass (potentially via
  the platform's design agent) once slices 1–2 land and give real tokens
  to design against.
- Not an implementation. No code is written here.
- Not a commitment to a schedule. Six slices with a single-agent operator
  is a multi-session engagement; each slice must be reviewed and merged
  before the next begins.
- Not a marketing plan. Product frame ("production engine") is discussed
  only insofar as it constrains copy in Slice 6.

---

## Appendix A — Tier-to-theme mapping (confirmed against code)

Source of truth: `portal/config.py::TIER_CONFIG` and
`portal/static/css/styles.css`.

| Tier | `--accent` | Concept logo | Notes |
|---|---|---|---|
| Explorer | `#86EDA5` (mint green) | "BRANDR EXPLORER" green logo | Free tier |
| Creator | `#A855F7` (purple) | "BRANDR CREATOR" purple logo | £6.99 / £4.99 founding |
| Studio | `#F5A623` (amber) | "BRANDR STUDIO" amber logo | £14.99 / £9.99 founding |
| Platinum | `#C0CFFF` (icy chrome blue) | "BRANDR PLATINUM" silver logo | £24.99 / £19.99 founding |
| Elite | `#C79619` (deep gold) | (no supplied logo — uses badge) | Invitation-only, `hidden: True` |
| Founding *(overlay)* | `#E8B923` (bright gold) | "BRANDR FOUNDING MEMBER — Early Access" gold logo | Applied via `data-tier="Founding"` on top of base tier |

Elite and Founding are visually similar golds but distinct: Elite is a
hidden invitation tier; Founding is an overlay skin for any founding-window
customer. This distinction survives the redesign; see Open Question 1.

---

## Appendix B — Anti-boxing checklist for PR review

Before merging any slice, verify:

- [ ] No new hard-coded `*WTF` string literals in new code.
- [ ] No new hard-coded country codes / flag paths in components.
- [ ] Any new component that renders a brand takes a brand-config object,
      not a specific brand's data.
- [ ] Any new axis-aware component (summary card, breadcrumb, review step)
      iterates the ProductionFormula's `.axes()`, not a hard-coded list.
- [ ] Any new colour value goes into `styles.css` as a token, referenced
      via `var(--…)`. No inline hex except in the token definitions.
- [ ] `prefers-reduced-motion` respected on any new animation.
- [ ] No new dependencies (no React, no Vue, no CSS-in-JS runtime).
- [ ] Slice is independently revertible via single `git revert`.

---

*End of plan.*
