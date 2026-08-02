# UI/UX Design — Dashboard (binding spec)

| | |
|---|---|
| Status | **BINDING** — stage-3 product UI spec, produced by the S3-T01 design session (2026-08-03) from the user's direction + the company brand book (`mockup/visual_guidelines/`). Supersedes the draft. The old mockup (`mockup/old_mockup.html`) contributed inspiration only (tab layout, humanoid figure, dark minimalist look); none of its implementation or values carry over. Structure/behavior AND visual design are now set; §8 holds the design tokens. |
| Related | [PRD.md](PRD.md) · [APPFLOW.md](APPFLOW.md) · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) · [biomech/SPEC.md](biomech/SPEC.md) §§2, 9, 10 |

## 1. Screen inventory & navigation — SET

| Route | Screen | Purpose |
|---|---|---|
| `/login` | Login | preset-account sign-in; the only unauthenticated route |
| `/` | Overview | explain-the-product hero + one panel per online device, projection-first |
| `/device/:id` | Device detail | one trainee: live left column + Insights/History/Projections tabs |

**Navigation is a left sidebar** (220px, `--surface` on `--bg`, hairline right border):
1. HIPPOS logo (full-logo white SVG from `mockup/visual_guidelines/Logo/svg/`), links `/`.
2. "Overview" nav item.
3. **Online devices** section: one row per online device — display name, live status
   dot, current composite as a small number tinted by risk band. Rows appear/disappear
   with the same 10 s rule as panels (§3). Click → `/device/:id`. Active route gets a
   2px `--accent` left rail + 4% accent wash (old-mockup pattern, kept).
4. Bottom block: WS connection dot (§6) + username + logout.

Tablet ≤1024px: sidebar collapses to a top bar (logo, overview link, device dropdown,
connection dot, user menu). Desktop-first; usable on a gym tablet.

## 2. Login — SET

Full-black (`--bg`) brand moment. Centered 400px card (`--surface`, radius 16):
logo mark (icon-only white SVG) above "HIPPOS" wordmark + "Motion Intelligence"
eyebrow, username, password, sign-in button (accent fill, black text). Inline error
on failure: "Invalid username or password" — never which one. Behind the card, the
brand mark at very low opacity with the §10 ambient ping animation (static under
`prefers-reduced-motion`). Redirects per APPFLOW §1.1; already-authed visits to
`/login` → `/`. 401/WS-4401 anywhere → `/login`.

## 3. Overview — SET

```
┌ sidebar ┐ ┌───────────────────────────────────────────────────────────────┐
│ HIPPOS  │ │ HERO (collapsible): animated figure | plain-words explainer   │
│ Overview│ │   + 6-metric mini-legend | live: N online · top projected     │
│ ─────── │ ├───────────────────────────────────────────────────────────────┤
│ Asha  42│ │ ┌ Panel: Asha K. ───────────────┐ ┌ Panel: dev-31 ──────────┐ │
│ dev-31 12│ │ │ ✏ name  ●online ▮▮▮▮▯ ····   │ │ …                       │ │
│ ─────── │ │ │ PROJECTED RISK   +10m         │ │                         │ │
│ ● conn  │ │ │   ▶ 62  (elevated)            │ │                         │ │
│ user ⎋  │ │ │   +30m 55 · +1h 47 · made 2m  │ │                         │ │
└─────────┘ │ │ now 45 ~~~sparkline~~~        │ │                         │ │
            │ │ ⚠ Reduce landing volume       │ │                         │ │
            │ └───────────────────────────────┘ └─────────────────────────┘ │
            └───────────────────────────────────────────────────────────────┘
```

**Hero strip** — the "anyone gets it in one look" section:
- Left: the animated humanoid figure (§10) — brand-cyan limbs, four sensor nodes
  (thigh/shin × L/R) with staggered sonar pings, data particles flowing along the
  limbs into a baseline. This is the product's "live data" signature.
- Middle: one-sentence explainer in plain language (§11 copy rules), then a
  6-metric mini-legend: color swatch + display name + one-clause tooltip each
  (from `lib/metrics.ts`, SPEC §9 names).
- Right: live squad stats as stat tiles — devices online, highest projected risk
  (name + value, risk-band tinted), active alerts count.
- **Collapsible**: an "About this data" toggle collapses the hero to a single slim
  row; state persists in `localStorage`. Daily users get density, first-time
  viewers get the story.

**Device panels** — one per **online** device; a device silent >10 s disappears
entirely (footer note "N offline hidden"; the online→offline badge flip still shows
during the 2–10 s window). New devices appear on first packet. Grid: 1–3 columns
responsive, cards `--surface` radius 16, hover raises border to `--border-hover`;
the whole card is one click target → `/device/:id` (rename control excepted).

Panel contents, top to bottom:
1. Header row: editable name (✏ inline: click → input, Enter saves via
   `PATCH /api/devices/:id`, Esc cancels, optimistic + rollback), online badge,
   quality meter (§6), four sensor micro-dots (§6) in sorted-limb order.
2. **Projected Injury Risk block — the panel's headline.** Closest configured
   horizon rendered as the stat-tile hero: label "Projected risk · +10m" (label
   text from `FUTURE_HORIZONS` config, never hardcoded), value ≥48px semibold
   sans (proportional figures), tinted by risk band (§6), band word beside it
   ("elevated"). Remaining horizons as a smaller inline stack: "+30m 55 · +1h 47".
   Micro-stamp "made 2m ago" (`made_at`). CI shown in the detail view, not here.
3. Secondary row: "now" — current live composite as a small number + 30 s live
   sparkline (uPlot, 60Hz, composite color, no axes, no legend).
4. Top active insight, action-first: severity chip (§6) + `action` text (fallback:
   `message`). One line, ellipsized.
5. Active biomech flag chips (§6), if any.
- No forecast yet → the "now" value is promoted to the headline slot with
  "First forecast in ~N min" beneath (N from `PREDICT_INTERVAL_S`).

## 4. Device detail — SET

```
┌ ← back  ✏ Asha K.  ● online  ▮▮▮▮▯ 98%   L-shin 641Hz ● · L-thigh ● · R-thigh ● · R-shin ● │
├──────────────────────────────────────────────┬──────────────────────────────────┤
│ LIVE                                         │ [Insights][History][Projections] │
│ ┌ figure ┐  INJURY RISK      ┌ flags ┐       │                                  │
│ │ (anim) │  ▶ 45 moderate ↗  └───────┘       │  (active tab content)            │
│ └────────┘                                   │                                  │
│ [composite — large uPlot, last 60 s]         │                                  │
│ [m1][m2][m3][m4][m5] compact stacked uPlots  │                                  │
└──────────────────────────────────────────────┴──────────────────────────────────┘
```

Two columns, `2fr 1fr` (stacks to one column ≤1024px, live first).

**Header:** back link, name (inline rename as §3), online badge, quality meter + %,
**per-limb sensor row**: for each mapped limb (sorted order) — limb label, live rate
("641Hz"), liveness dot (§6). A limb that has never streamed shows its dot in
critical with "no data". Active flag chips right-aligned.

**Left column — LIVE:**
- Top row: the humanoid figure (compact variant, §10) **driven by real data**:
  sensor dots lit by per-sensor liveness (from `/api/devices` sensors + tick flow),
  ping animation running only while the device streams; when `m5` is non-null the
  higher-loaded leg's glow is slightly stronger (no numeric claim, no side label —
  SPEC §5.5 forbids directional claims; this is ambient emphasis only, capped subtle).
  Beside it, **current Injury Risk as the view's single hero figure**: ≥48px
  semibold sans, risk-band tint, band word, trend arrow vs 5 min ago.
  Below the pair: the SPEC §2 one-liner: "A monitoring aid, not a prediction."
- Large composite live chart: uPlot, last 60 s, 2px `--composite` line, 10% area
  wash, hairline grid, y fixed 0–100, risk-band thresholds as faint hairlines.
- Five compact stacked primitive charts (~64px tall each): label + current value
  (ink, mark-colored dot key), 2px line in the metric's series color, y 0–100.
  `null` renders as a gap — never 0; while `warming_up`, the panel is greyed with
  a muted "warming up" chip; `degraded_sensors`/`partial` grey it with the §6
  warning/alert chip instead ("no data from R-shin"). The two states must never
  look alike (§6).

**Right column — three pill tabs** (old-mockup pattern: pill, inactive = muted text
+ transparent border, active = accent text + accent hairline border + 4% wash;
proper `tablist`/`tab`/`tabpanel` roles, arrow-key navigation):

1. **Insights** (`GET /api/insights?device`, poll 30 s) — newest first. Each card:
   severity chip + icon (§6), then the **`action` as an imperative headline**
   (semibold, primary ink — color lives in the chip, never the text), then
   `rationale` in secondary ink (the why, from measured metrics), then an
   "evidence" expander rendering the `context` values, then relative time.
   Rows missing `action`/`rationale` (pre-refinement rules) render `message` as
   the headline. Severity also paints a 3px left border on the card.
2. **History** (`GET /api/metrics/history?device&window`, poll 60 s) — a period
   selector row at the top: one segmented control listing the configured
   `PAST_WINDOWS` labels ("past 5m / 30m / 2h" — generated from config), one
   filter scoping every chart below it. Under it, **six small-multiple bar
   charts** (composite first, then m1–m5), each: title + latest-bucket value,
   single-series bars in that metric's color (≤24px wide, 4px rounded top, 2px
   surface gaps, square baseline), y 0–100, hairline grid, per-bar hover tooltip
   (t, value, quality). Composite's chart adds a min–max whisker per bucket.
   Buckets with no rows render as gaps. Partial coverage: "collecting… (n min of
   5m)". A table-view toggle (one table for all metrics × buckets) sits at the
   row's right end — every charted value is reachable without hover.
3. **Projections** (`GET /api/forecasts/latest?device`, poll 60 s) — composite
   only (PRD; confirmed). Top: per-horizon stat row mirroring the §3 stack.
   Below: one ECharts chart — recent actuals (solid 2px composite line, from the
   shortest configured history window) continuing into per-horizon forecast
   points (≥8px markers with 2px surface ring) with a CI band (composite hue at
   10% opacity); "made at HH:MM · linreg-stub-1" stamp in muted ink. Crosshair +
   tooltip; table-view toggle (horizon, prediction, CI). Empty: "First forecast
   in ~N min."

Offline device: live charts freeze with "offline — last seen HH:MM:SS" overlay;
after 10 s the sidebar/overview entries hide (§3) but a directly-open detail page
stays, frozen, with the overlay (deep links must not go blank).

## 5. Live chart behavior — SET (unchanged from draft)

- uPlot, canvas-rendered, one rolling buffer per device (default 60 s @ 60Hz = 3,600
  points; append + shift, redraw on rAF). No SVG chart libs for live data.
- On mount: REST backfill (`/api/metrics/recent?seconds=30`) then splice WS stream;
  small (~250 ms) render delay absorbs network jitter for a smooth line.
- Hidden tab (`visibilitychange`): pause rendering, drop incoming to newest; on
  return, re-backfill and resume.
- WS drop/reconnect: exponential backoff 1 s→10 s; charts freeze, connection dot
  amber; on reconnect, backfill gap via REST.
- Offline device: freeze + overlay per §4.

## 6. Severity, status & flags vocabulary — SET (colors now bound to §8 tokens)

| State | Rendering |
|---|---|
| info | chip: ℹ icon + "info", `--series-m1` blue tint |
| warning | chip: ⚠ icon + "warning", `--status-warning` |
| alert | chip: ⛔ icon + "alert", `--status-critical` |
| online / offline | dot `--status-good` / `--ink-3`; offline >10 s hidden (§3) |
| connection (WS) | dot: `--status-good` connected / `--status-warning` reconnecting |
| quality | % + 5-bar meter: fill `--status-good` ≥90, `--status-warning` 60–90, `--status-critical` <60; track = same hue at 20% opacity |
| sensor liveness | micro-dot: fresh `--status-good`; stale (no packets ≤10 s) `--status-warning`; never-streamed/dead `--status-critical`; tooltip "limb · rate · last seen" |
| risk bands (0–30/30–60/60–80/80–100) | low `--status-good` · moderate `--ink-2` (neutral) · elevated `--status-warning` · high `--status-critical`; band word always accompanies the color |

Chips always pair icon + label — color never carries meaning alone.

**Biomech flags — MUST be rendered** (tick `f` field, BACKEND_SCHEMA §2; the UI is
the calibration story's only surface, SPEC §10):

| Flag | Means | Weight |
|---|---|---|
| `cal_failed` | sensor motionless but disagrees with gravity — hardware fault | alert |
| `degraded_sensors` | fewer sensors than mapped / one never streamed: the metric is **never** coming | alert |
| `saturated` | clipped window; `m1`/`m2` suppressed | alert |
| `uncalibrated` | running on defaults; `m4`/`m5` carry gain bias | warning |
| `partial` | a required sensor went inactive mid-session | warning |
| `no_shank` | impact falls back to all limbs | warning |
| `carried_over` | calibrated from a previous session, not measured today | info |
| `warming_up` | `m4`/`m5` inside 60 s / 30 s warm-up — a value **is** coming | muted |
| `unvalidated` | `m4`/`m5` have no real-data validation yet (SPEC §11.1) | muted |

`warming_up` and `degraded_sensors` must never look alike: muted grey chip + greyed
panel vs alert chip + explicit "no data from <limb>". The uncalibrated→calibrated
transition is a visible step in `m4`/`m5`: when `uncalibrated`/`carried_over` clears,
show a muted "calibrated ✓" chip for ~10 s — a system event, not a change in the
athlete (SPEC §3.8).

## 7. Empty & error states — SET

- No devices online: hero stays; panel area shows "Waiting for devices… point
  wearables at `<IP>:<UDP_PORT>`" (+ "N offline hidden" when applicable).
- No insights: "No insights yet — all metrics in normal range."
- History without enough data: "collecting… (n min of 5m)".
- Forecast before first run: "Waiting for the first projection run…" (the client
  does not know `PREDICT_INTERVAL_S`, so no minute estimate is shown).
- API errors: non-blocking toast + retry; stale panels hold last render at reduced
  opacity (never a skeleton flash on refetch); auth failures → `/login`.

## 8. Design tokens — SET (source of truth for `frontend/src/theme.css`)

Brand cyan sampled from `mockup/visual_guidelines/Color Pattern` (gradient
#21F3FC → #2BBECD on black). Chart palette **machine-validated** (dataviz
six-checks) against `--surface` #0D0D0D, 2026-08-03: 5-slot categorical — all
checks PASS (lightness band, chroma, CVD ΔE worst adjacent 8.4, normal-vision
19.3, contrast ≥3:1). Status colors from the validated reference status set.

```css
:root {
  /* surfaces */
  --bg: #000000;            /* page */
  --surface: #0D0D0D;       /* cards, charts */
  --surface-2: #141414;     /* raised: hover, chips, inputs */
  --surface-3: #1B1B1B;     /* inset: meter tracks, code */
  --border: rgba(255,255,255,0.08);
  --border-hover: rgba(255,255,255,0.14);
  /* ink — text never wears series colors */
  --ink: #FFFFFF;
  --ink-2: #C3C2B7;
  --ink-3: #898781;         /* axis/labels; ≥3:1 on --surface */
  /* brand */
  --accent: #21F3FC;
  --accent-deep: #2BBECD;
  --accent-grad: linear-gradient(135deg, #21F3FC, #2BBECD);
  /* chart series (fixed order, never re-assigned on filter) */
  --composite: #21F3FC;     /* Injury Risk — only ever charted alone */
  --series-m1: #3987E5;     /* Impact */
  --series-m2: #D95926;     /* Loading Rate */
  --series-m3: #199E70;     /* Accumulated Load */
  --series-m4: #C98500;     /* Movement Control */
  --series-m5: #D55181;     /* L/R Balance */
  /* status (reserved — never used as series colors) */
  --status-good: #0CA30C;
  --status-warning: #FAB219;
  --status-serious: #EC835A;
  --status-critical: #D03B3B;
  /* shape & type */
  --radius-s: 6px;  --radius-m: 10px;  --radius-l: 16px;
  --font: 'Inter', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', monospace;  /* micro-eyebrows only, never values */
}
```

- **Typeface:** Inter (free, licensed for production) for ALL text and numbers —
  TT Hoves Pro is trial-licensed and appears only inside the pre-rendered logo
  SVGs. JetBrains Mono is limited to decorative micro-eyebrows (e.g. "BILATERAL ·
  4 SENSORS"); values/labels/axes are Inter. Hero/stat values: semibold,
  proportional figures; `font-variant-numeric: tabular-nums` only in tables and
  axis ticks.
- Type scale: 11 (micro), 13 (body-s), 15 (body), 18 (h3), 24 (h2), 32 (h1),
  48+ (hero figures). Micro-eyebrows: 11px uppercase, letter-spacing 0.08em.
- Spacing: 4px base grid; card padding 20; grid gaps 16; section gaps 28.
- Dark theme only for the MVP (brand is black; no light variant shipped).

## 9. Chart rules — SET (dataviz method; check anti-patterns before implementing)

- Marks: bars ≤24px thick, 4px rounded data-end, square baseline, 2px surface
  gaps; lines 2px round-cap; markers ≥8px with 2px surface ring; area washes at
  ~10% series opacity; grid/axes solid hairlines one step off surface — never
  dashed gridlines (dashing is reserved for the forecast continuation, where
  "projection" is exactly the meaning).
- One y-axis per chart, fixed 0–100 for all metric charts (`q` charts 0–1).
  Never dual-axis; compare metrics via the small multiples, not overlays.
- Single-series charts (every metric chart here) carry no legend box — the title
  names the series; identity across the app = the fixed metric→color map.
  The hero mini-legend (§3) is the one place all six swatches appear together.
- Direct labels selectively (latest value, extremes); everything else via axis +
  hover tooltip; every aggregate chart has a table-view twin (§4). Text wears ink
  tokens; series color appears only in marks and swatches.
- Live charts render at 60Hz on canvas (§5); hover crosshair + tooltip on
  aggregate charts; hit targets ≥24px.
- No emoji as icon system — a single line-icon set (e.g. Lucide), stroke 1.5–2px.

## 10. Brand & motion — SET

- **Humanoid figure** (rebuilt, not copied): SVG, headless-mannequin silhouette,
  limbs as rounded strokes in `--accent-grad` fading distally; four sensor nodes
  at thigh/shin × L/R with breathing dots + expanding sonar rings, staggered
  0/.4/.8/1.2 s; 2–3 data particles per leg flowing along the limb path.
  Implemented with CSS `offset-path` / Web Animations — **no SMIL**. Two
  variants: hero (~380px, with radial glow + slow scan line + dot-matrix
  backdrop) and compact (~190px, detail page, data-driven per §4).
- Motion principles: slow, ambient, sub-1Hz loops; 150 ms ease transitions on
  interactive elements; needle/value changes ease 600 ms. Nothing blinks.
  `prefers-reduced-motion`: all loops stop (static figure, dots lit), transitions
  remain.
- Logo usage: white-on-black variants only; never recolor; icon-only mark for
  favicon (black background tile) and login.

## 11. Copy rules — SET (binding, from biomech SPEC §2)

- The composite is a **monitoring/triage aid shown as a trend — never a verdict**.
  Say "projected risk", "elevated load", "deviating from baseline", "flag for
  review". Never "predicts injury", "X% chance of injury", "bone load", "tibial
  stress".
- Never a directional L/R claim ("left leg is weaker") — SPEC §5.5. `m5` copy
  speaks of imbalance magnitude only.
- Any alert/insight shows the evidence that fired it (context expander, §4).
- The detail view carries the standing one-liner: "A monitoring aid, not a
  prediction." Plain language everywhere; the trainer is not technical.

## 12. Accessibility & responsive — SET

- Real `<button>`/`<a>` for every interactive element; visible `--accent` focus
  ring; tabs use `tablist`/`tab`/`tabpanel` + arrow keys; rename input:
  Enter/Esc; modals (if any) trap focus + Esc.
- Charts: table-view twins (§9); tooltips duplicated on keyboard focus; figure
  SVGs get `role="img"` + `aria-label`; live regions announce new alerts
  (`aria-live="polite"`).
- Contrast: ink tokens ≥4.5:1 on their surfaces; `--ink-3` used ≥11px only;
  status-on-surface ≥3:1 (validated).
- Breakpoints: ≤1024px sidebar → top bar, detail stacks (live first); ≤680px
  panels single-column, hero auto-collapses.

## 13. Data/API mapping — SET (frontend consumes BACKEND_SCHEMA §3 only)

| Widget | Source | Cadence |
|---|---|---|
| sidebar devices, panels registry, sensor rows | `GET /api/devices` | 10 s poll + WS `status` events |
| live sparkline/charts, current values, flags | `WS /ws/live` (+ `GET /api/metrics/recent` backfill) | 60Hz |
| projected-risk blocks, Projections tab | `GET /api/forecasts/latest` | 60 s poll |
| History tab | `GET /api/metrics/history` (window ∈ `PAST_WINDOWS`) | 60 s poll |
| insight chips + Insights tab | `GET /api/insights` (`action`/`rationale` fields) | 30 s poll |
| rename | `PATCH /api/devices/:id` | on action |
| auth | `POST /api/auth/login|logout`, `GET /api/auth/me` | on action |

Frontend constants (`lib/config.ts`): `OFFLINE_HIDE_MS = 10_000`, poll intervals
above, metric map (`lib/metrics.ts` — SPEC §9 names/tooltips + §8 colors). Window
and horizon labels are always generated from config strings — never hardcoded.
