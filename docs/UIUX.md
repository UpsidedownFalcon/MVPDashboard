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

**Navigation is a left sidebar** (224px, `--surface` on `--bg`, hairline right border):
1. HIPPOS logo — the `<LogoFull />` inline SVG component (paths taken from
   `mockup/visual_guidelines/Logo/svg/`; see §10 for why it must be inline), links `/`.
2. "Overview" nav item.
3. **Athletes online** section: one row per online device — display name, live status
   dot, current composite as a small number tinted by risk band. Rows appear/disappear
   with the same 10 s rule as panels (§3). Click → `/device/:id`. Active route gets a
   2px `--accent` left rail + 5% accent wash (old-mockup pattern, kept).
4. Bottom block: WS connection dot (§6) + username + logout.

Tablet ≤1024px: sidebar collapses to a top bar (logo, overview link, the full device
list kept as a horizontally scrolling row — no dropdown, connection dot, user menu).
Desktop-first; usable on a gym tablet.

## 2. Login — SET

Full-black (`--bg`) brand moment. Centered 380px card (`min(380px, 92vw)`;
`--surface`, radius 16): logo mark (icon-only white SVG, the `LogoMark` — inside
the card) above "HIPPOS" wordmark + "Motion Intelligence" eyebrow, username,
password, sign-in button (accent fill, black text). Inline error on failure:
"Invalid username or password" — never which one; a rate-limit 429 reads "Too
many attempts — wait a minute and try again." Behind the card, a large
low-opacity cyan glow that breathes (no brand mark behind the card; static under
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
- Left: the animated humanoid figure (§10) — limbs lit per-liveness, four sensor
  nodes (thigh/shin × L/R) with staggered sonar pings, data particles travelling
  down each instrumented bone (there is no baseline/ground element). This is the
  product's "live data" signature.
- Middle: eyebrow + headline + short paragraph in plain language (§11 copy rules),
  then a 6-metric mini-legend: color swatch + display name + one-clause tooltip each
  (from `lib/metrics.ts`, SPEC §9 names).
- Right: live squad stats as stat tiles — "Athletes online", highest projected risk
  (name + value, risk-band tinted; falls back to a "Highest risk now" tile when no
  forecasts exist yet), and "Alerts · 30 min" (alert-severity insights squad-wide
  in the last 30 minutes).
- **Collapsible**: an "About this data" toggle collapses the hero to a single slim
  row; state persists in `localStorage`. Daily users get density, first-time
  viewers get the story.

**Device panels** — one per **online** device; a device silent >10 s disappears
entirely (footer note "N offline hidden"; the online→offline badge flip still shows
during the 2–10 s window). New devices appear on first packet. Grid: 1–4 columns
responsive (auto-fill, 330px min), cards `--surface` radius 16, hover raises border to `--border-hover`;
the whole card is one click target → `/device/:id` (rename control excepted).

Panel contents, top to bottom:
1. Header row: editable name (✏ inline: click → input, Enter saves via
   `PATCH /api/devices/:id`, Esc cancels, optimistic + rollback), online badge,
   **calibration badge** (§6a) while settling, quality meter (§6), four sensor
   micro-dots (§6) in sorted-limb order, and the **battery** (§6b) top-right.
2. **Projected Injury Risk block — the panel's headline.** Closest configured
   horizon rendered as the stat-tile hero: label "Projected risk · +10m" (label
   text from `FUTURE_HORIZONS` config, never hardcoded), value ≥48px semibold
   sans (proportional figures), tinted by risk band (§6), band word beside it
   ("elevated"). Remaining horizons as a smaller inline stack: "+30m 55 · +1h 47".
   Micro-stamp "made 2m ago" (`made_at`). CI shown in the detail view, not here.
3. Secondary row: "now" — current live composite as a small number + 30 s live
   sparkline (uPlot, 60Hz, composite color, no axes, no legend).
4. Top active insight, action-first: severity chip (§6) + `action` text (fallback:
   `message`); the `data_quality` rule is excluded from this slot (demo posture
   2026-08-05). One line, ellipsized.
5. Active biomech flag chips (§6), if any.
- No forecast yet → the "now" value is promoted to the headline slot with
  "waiting for the first projection…" beneath. **No minute estimate**: the client is never
  told `PREDICT_INTERVAL_S`, and inventing a number would be a guess.

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

Two columns, `2fr 1fr` (stacks to one column ≤1180px, live first).

**Header:** back link, name (inline rename as §3), online badge, calibration badge (§6a)
while settling, quality meter + %, **per-limb sensor row**: for each mapped limb (sorted
order) — limb label, live rate ("641Hz"), liveness dot (§6). A limb that has never streamed
shows its dot in critical — "never streamed" lives in the dot's tooltip, not in visible
text. Active flag chips, then the **battery** (§6b) top-right.

**Left column — LIVE:**
- Top row: the humanoid figure (compact variant, §10) **driven by real data**:
  sensor dots lit by per-sensor liveness (from `/api/devices` sensors + tick flow),
  ping animation running only while the device streams; when `m5` is non-null the
  OTHER leg is dimmed to ~0.55 alpha — ambient de-emphasis matching the
  side label on the `m5` row, with nothing emphasised when the split reads
  "even" (updated 2026-08-03: BACKEND_SCHEMA §2 sanctions a
  **neutral** side readout; what SPEC §5.5 forbids is the *claim* — "weaker", a
  finding, or any cross-session comparison — not the factual side).
  Beside it, **current Injury Risk as the view's single hero figure**: ≥48px
  semibold sans, risk-band tint, band word, trend arrow vs 5 min ago.
  Below the pair: a one-liner. Demo posture (2026-08-05): "Computed live from every
  impact and stride." (was the SPEC §2 hedge "A monitoring aid, not a prediction" —
  restore when the demo posture ends).
- Large composite live chart: uPlot, last 60 s, 2px `--composite` line, 10% area
  wash, hairline grid, y fixed 0–100, risk-band thresholds as faint hairlines.
- Five compact stacked primitive charts (~56px tall each): label + current value
  (ink, mark-colored dot key), 2px line in the metric's series color, y 0–100 —
  **except `m5`, which is signed and uses −100..+100 with a hairline at 0**, its
  row showing `|m5|` plus the neutral side label ("more load left").
  When the tick carries `saturated`, `m1`/`m2` render as **"≥ x"** — they are
  lower bounds, not exact values (BACKEND_SCHEMA §2).
  `null` renders as a gap — never 0, and a measured 0 is a real value, not a gap;
  while `warming_up`, the panel is greyed with a muted "warming up" chip;
  `degraded_sensors`/`partial` grey it with the §6 warning/alert chip instead —
  the chip shows the flag's generic label ("sensors missing" / "partial data");
  the affected limb is named only in the sensor-dot tooltips. The two states
  must never look alike (§6).

**Right column — exactly three pill tabs** (old-mockup pattern: pill, inactive =
muted text + transparent border, active = accent text + accent hairline border +
5% wash (segmented controls use 7%); proper `tablist`/`tab`/`tabpanel` roles,
arrow-key navigation):

1. **Insights** — the advice **timeline** (since 2026-08-06), as one flat
   chronological stack of cards. Source: `GET /api/insights/timeline?device`
   (poll 10 s, `POLL_ADVICE_MS`), which is already bucketed by age over the
   same `PAST_WINDOWS` as the History tab, grouped, deduped, capped at
   ≤ `INSIGHT_MAX_ACTIONS` per time base and ordered server-side — **the
   client must not repeat any of that**. This is what makes advice survive a
   page reload: insights were always persisted (`/api/insights`), the panel
   just used to read only the 150 s "currently standing" view. Because the
   route drops `context` and the long `rationale`, the panel also fetches
   `GET /api/insights?device&limit=100` and joins on `(rule_id, created_at)`
   purely to recover evidence.

   **Stack rules — SET:**
   - **No time toggle** (unlike History). All cards stack; the age reads off
     the card itself.
   - **Chronological**: latest at the top, oldest at the bottom — buckets
     arrive newest-first (`live`, then `past 5m / 30m / 2h`) and cards are
     newest-first within each bucket.
   - **Top-right age label**: the card's time base — `live`, or
     `windowLabel()` of its bucket ("past 5m"…), never a hardcoded duration;
     the exact timestamp + "updated Ns ago" live in the hover tooltip.
   - **≤ 3 cards per time base** (`INSIGHT_MAX_ACTIONS`, server-enforced).
   - **Left edge = severity hue faded by age**: the 3px border keeps the
     severity colour but its strength steps down per bucket
     (`color-mix(severity, transparent)`, live = 100% → oldest ≈ 32%,
     recomputed from the bucket count so a `PAST_WINDOWS` change reshapes the
     ramp automatically). Lightest = newest, darkest = oldest.
   - The **same action may recur in several buckets** — a condition that kept
     firing is a story, not a duplicate; keys are bucket-qualified.

   **Card anatomy, in order** — the first line is what to do, so the eye lands
   on the action before anything else:
   1. severity chip + icon (§6) and the age label top right (demo posture
      2026-08-05: the `unvalidated metric` chip is not rendered;
      `action.unvalidated` still arrives from the API for when validation
      exists);
   2. **the action as a large bold headline** (22px/700, primary ink — colour
      lives in the chip and the 3px age-faded severity left border, never the
      text);
   3. **blank vertical space — no separator rule** (an `<hr>` here reads as a
      divide between two things rather than one card);
   4. the rationale as ordinary sentences, one paragraph per supporting reason,
      falling back to the short `reason` text when the join misses;
   5. the **static coaching cue** (`tip`), under a **"Coaching cue"** label
      (demo posture; was "General cue — not measured") — it is catalogue text,
      identical every firing;
   6. the **Evidence expander** (`<details>`) over the joined `context`.

   An empty timeline is a calm empty state, never a warning — it is the normal
   early-session condition. `aria-live="polite"` announces new advice.
2. **History** (`GET /api/metrics/history?device&window`, poll 60 s) — a period
   selector row at the top: one segmented control listing the configured
   `PAST_WINDOWS` labels ("past 5m / 30m / 2h" — generated from config), one
   filter scoping every chart below it. Under it, **six small-multiple bar
   charts** (composite first, then m1–m5), each: title + latest-bucket value,
   single-series bars in that metric's color (≤24px wide, 4px rounded top,
   proportional 25% category gaps, square baseline), y 0–100 (m5: −100..+100,
   hairline at 0), hairline grid, per-bar hover tooltip
   (t, value, quality). Composite's chart adds a min–max whisker per bucket.
   Buckets with no rows render as gaps. Low coverage (<50%) shows a warning chip
   "partial · N% of window"; a window with no data at all reads "collecting… no data yet in
   past X". Bucket counts are chosen to divide the window **exactly** (`evenBucketCount`),
   so bars have uniform span and labels do not drift. A table-view toggle (one table for all
   metrics × buckets) sits at the row's right end — every charted value is reachable without hover.
3. **Projections** (`GET /api/forecasts/latest?device`, poll 60 s) — composite
   only (PRD; confirmed). **The horizon set is not fixed** — it starts 1m/2m and
   becomes 10m/30m/1h — so horizons are always read from `points` and never
   assumed to be three. Demo posture (2026-08-05): the "early projection ·
   treat as provisional" banner is not rendered (`provisional` still arrives
   from the API); both models produce a genuine statistical prediction
   interval, and the band note reads "Shaded band shows the projection
   interval."
   Top: per-horizon stat row mirroring the §3 stack (labels use the shorter
   "Projected +10m" form here).
   Below: one ECharts chart — recent actuals (solid 2px composite line, from the
   shortest configured history window) continuing into per-horizon forecast
   points (≥8px markers with 2px surface ring) with a CI band (composite hue at
   10% opacity); "Made <relative time> · <model_version from the response>" stamp in muted
   ink — **never a hardcoded version string**. Crosshair +
   tooltip; table-view toggle (horizon, prediction, CI). Empty: "First
   projection in a couple of minutes…" (§7).

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
| **risk bands (0–10 / 10–25 / 25–45 / 45–100)** | low `--status-good` · moderate `--ink-2` (neutral) · elevated `--status-warning` · high `--status-critical`; band word always accompanies the color |

**⚠️ Band cutoffs re-cut 2026-08-03** (`RISK_BAND_CUTOFFS` in `lib/metrics.ts` is the
implementation). The original 30/60/80 split was chosen when accumulated dose entered
the composite as an additive floor. After the dose floor was removed (biomech SPEC
§6.1a) a **fresh** athlete measures: squats 1.3, walk 2.6, jog 6.0, kick 8.4,
single-leg landing 17, jump 21 — and the *same* jump when fatigued reads ~33. Against
30/60/80 an entire worn protocol including fatigue collapses into "low", so the band
would carry no information. The new cutoffs are anchored on those measured landmarks:
low = ordinary activity; moderate = real athletic loading; **elevated = the same work
costing more than it should — the measured fresh→fatigued jump crosses here**, which is
the capacity model's whole point; high = beyond anything the protocol produced fresh.
These are **display bands only** — the backend's `INSIGHT_WARN/ALERT_THRESHOLD` (85/92)
is a separate, backend-owned calibration and is *not* implied by them.

Chips always pair icon + label — color never carries meaning alone.

### 6a. Calibration badge — SET

A **"Stand still · Ns" countdown** chip appears on the overview card and the detail header
while a device is calibrating, then a brief verdict — **"Calibrated"** (the icon supplies
the tick; there is no ✓ character in the label) or **"Calibration failed"** — for ~8 s. Its
whole job is to tell the athlete *how much longer to stand still*, and whether it worked.

It is driven **only** by the tick's `cal` field (BACKEND_SCHEMA §2) and `cal_failed` — but
**bounded** (user decision 2026-08-04): when the device appears and `cal` first arrives, the
UI latches a wall-clock deadline of `min(cal, 20 s)` and counts down **monotonically** — the
displayed number never rises, and when it reaches zero the badge **disappears regardless of
backend state**, with no verdict. The backend's `cal` still honestly rises when the athlete
moves; the UI simply refuses to show an unbounded ask, because a badge that can blink forever
teaches people to ignore it. If the backend resolves within the cap, the verdict shows as
before; after a timeout, the `uncalibrated` / `carried_over` chips carry the state.

🚩 **It must NEVER be driven by `warming_up`.** That flag is `m4`/`m5`'s warm-up, which needs
**60 s / 30 s of MOVEMENT** to clear, while calibration needs **stillness** — the two are
mutually exclusive, so a badge watching both can never stop while the athlete stands still.
That was a real shipped bug: an owner stood still for over 30 s and the badge span forever.
`carried_over` is excluded for a different reason — it means "running last session's values",
which is a state, not a wait, and it has its own info chip.

**Shown only at the start of a session** (user decision 2026-08-04). The verdict latch resets
only on a **real absence** — silent longer than `OFFLINE_HIDE_MS` (10 s), i.e. the device has
dropped out of the UI entirely and its return counts as a new session. It deliberately does
**not** key on the `online` flag, which flips after ~2 s: a brief packet dropout mid-session
would otherwise re-arm the badge and restart the count. (The verdict RESET never keys on
`online`; ARMING does — a badge cannot arm mid-dropout.)

**Also worth knowing** (backend investigation 2026-08-04, revised later the same day): the
first investigation declared calibration sound; the second found the real fault. The stillness
guard rejected any tick whose `|a|` was >2% off gravity while the k guard called up to 5%
correctable — so a motionless sensor 2–5% off *never* accumulated a window, was branded
`cal_failed` at 20 s, and (because `cal_failed` sensors still counted into `cal`) pinned the
countdown forever. Both are fixed: the guard is now 6% (SPEC §3.8) and `cal_failed` sensors
leave the countdown, which therefore reaches null once every healthy sensor is measured. The
UI's 20 s hard cap above is the belt-and-braces on top of that.

### 6b. Battery — SET

Phone-style icon + percentage, **top-right** of the overview card and the detail header.
Source: `soc` on `GET /api/devices`, which is already the **minimum across the device's two
leg MCUs** — a flat unit must not hide behind a healthy one. Amber ≤20%, red + a slow pulse
≤10%; at ≤10% a bolt glyph also appears inside the shell. **`null` renders nothing at all**, never 0%: the SD-log decode path synthesises 0, so a
zero would be indistinguishable from "no reading yet".

**Biomech flags — MUST be rendered** (tick `f` field, BACKEND_SCHEMA §2; the UI is
the calibration story's only surface, SPEC §10):

| Flag | Means | Weight |
|---|---|---|
| `cal_failed` | sensor motionless but disagrees with gravity — hardware fault | alert |
| `degraded_sensors` | fewer sensors than mapped / one never streamed: the metric is **never** coming | alert |
| `saturated` | clipped window; `m1`/`m2` are **lower bounds** — rendered "≥ x", never as exact (BACKEND_SCHEMA §2) | alert |
| `uncalibrated` | running on defaults; `m4`/`m5` carry gain bias | warning |
| `partial` | a required sensor went inactive mid-session | warning |
| `no_shank` | impact falls back to all limbs | warning |
| `carried_over` | calibrated from a previous session | info |
| `warming_up` | `m4`/`m5` inside 60 s / 30 s warm-up — a value **is** coming | muted |
| `unvalidated` | **not rendered** (demo posture 2026-08-05, `HIDDEN_FLAGS` in `metrics.ts`); still on the wire per SPEC §11.1 | — |

`warming_up` and `degraded_sensors` must never look alike: muted grey chip + greyed
panel vs alert chip + explicit "no data from <limb>". The uncalibrated→calibrated
transition is a visible step in `m4`/`m5`: when `uncalibrated`/`carried_over` clears,
show a muted "calibrated ✓" chip for ~10 s — a system event, not a change in the
athlete. ⚠️ **NOT YET IMPLEMENTED** — `FlagChips` renders only flags that are currently
present; nothing tracks the clear transition. Tracked as outstanding UI work.
Original rationale: the step change is in the
athlete (SPEC §3.8).

## 7. Empty & error states — SET

- No devices online: hero stays; panel area shows "Waiting for devices… point
  wearables at this server's UDP port." (+ "N offline hidden" when applicable).
- No insights: "Nothing to flag right now" + "Advice appears here within about a
  minute of something worth acting on."
- History without enough data: "collecting… no data yet in past X"; partial coverage shows a
  "partial · N% of window" chip beside the period selector.
- Projections tab before the first run: "First projection in a couple of minutes…"
  — the tab may hint at scale; the **overview card** still shows no minute
  estimate (the client is never told `PREDICT_INTERVAL_S`).
- API errors: inline per-panel "…retrying…" notices — no toast exists, and there
  is no reduced-opacity treatment; the advice panel holds its previous data via
  `placeholderData`. Auth failures → `/login`.

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
  --font: 'Inter Variable', 'Inter', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', monospace;  /* micro-eyebrows only, never values */
}
```

- **Typeface:** Inter (free, licensed for production) for ALL text and numbers —
  TT Hoves Pro is trial-licensed and appears only inside the pre-rendered logo
  SVGs. JetBrains Mono is limited to decorative micro-eyebrows (e.g. "BILATERAL ·
  4 SENSORS"); values/labels/axes are Inter. Hero/stat values: semibold,
  proportional figures; `font-variant-numeric: tabular-nums` in tables, axis
  ticks **and any in-place-ticking value** (battery %, calibration countdown,
  sensor rates, live values, history cells, evidence values).
- Type scale (as shipped): 11 / 11.5 / 12 / 12.5 / 13 / 13.5 / 15 / 17 / 22 /
  26 / 30 / 32 / 52 — main steps: 11 micro, 13 body-s, 15 body, 22 insight-action
  headline, 32 h1, 52 hero figures. Micro-eyebrows: 11px uppercase,
  letter-spacing 0.08em.
- Spacing: 4px base grid; card padding 20; grid gaps 16; section gaps 28.
- Dark theme only for the MVP (brand is black; no light variant shipped).

## 9. Chart rules — SET (dataviz method; check anti-patterns before implementing)

- Marks: bars ≤24px thick, 4px rounded data-end, square baseline, proportional
  25% category gaps (a measured zero draws a 2px stub — a real value, not a
  gap); lines 2px round-cap; markers ≥8px with 2px surface ring; area washes at
  ~10% series opacity; grid/axes solid hairlines one step off surface — never
  dashed gridlines (dashing is reserved for the forecast continuation, where
  "projection" is exactly the meaning).
- One y-axis per chart, fixed 0–100 for all metric charts — except signed `m5`,
  which uses −100..+100 with a hairline at 0, in both live and history.
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

- **Humanoid figure — genuinely 3D** (rebuilt on canvas 2026-08-04, replacing the
  flat SVG; rebuilt again 2026-08-05 as a **full-body point cloud**, commit
  b8c19c8 — `frontend/src/components/HumanoidFigure.tsx`). Real perspective
  projection: the figure **rotates slowly about Y** (~15 s per turn), and the
  **whole body — head, torso, arms, legs, feet — is one depth-sorted point
  cloud** sampled from anatomically proportioned capsules + ellipsoids. The four
  instrumented bones (thigh/shin × L/R) carry a glow underlay, and their point
  clusters are denser and lit in the limb's liveness colour (no separate bright
  core stroke); data particles travel down each bone, and the four sensor nodes
  pulse with expanding sonar rings — pings and particles run only on live
  ('good') limbs. The non-instrumented body renders in dim `--accent-deep` teal;
  a mapped limb with no state renders dark. The cloud is sampled once by a
  deterministic LCG (no shimmer between mounts), with a per-point shimmer phase.
  Depth sorting means the far side genuinely passes
  behind the near side. Two variants: hero (200×380, with radial glow + scanning
  ring) and compact (120×220, detail page, data-driven per §4).
  **No 3D library** — the scene is ~1,850 points and four instrumented bones
  (thigh/shin × L/R), and three.js
  would add ~600 KB for it. The component's props are unchanged from the SVG
  version, so per-limb liveness colours, `active` and the `m5` side emphasis all
  carry over. `prefers-reduced-motion` freezes rotation and pulses; the figure
  still renders.
- Motion principles: slow, ambient, sub-1Hz loops; 150 ms ease transitions on
  interactive elements; needle/value changes ease 600 ms. Nothing blinks.
  `prefers-reduced-motion`: all loops stop (static figure, dots lit), transitions
  remain.
- Logo usage: white-on-black variants only; never recolor; icon-only mark for
  favicon (black background tile) and login.
  ⚠️ **The marks are INLINE SVG React components** (`frontend/src/components/Logo.tsx`),
  not `<img src="…​.svg">`. An SVG loaded through `<img>` is an isolated document, so its
  `fill="currentColor"` resolves against the SVG's own default colour — black — and
  `filter: brightness(10)` cannot rescue it, because brightness *multiplies* and 0 × 10 = 0.
  That combination rendered the logo pure black on a black page. Inlining lets `currentColor`
  inherit the real CSS `color`, so `color: var(--ink)` is all that is needed.

## 11. Copy rules — SET (binding, from biomech SPEC §2)

- The composite is a **monitoring/triage aid shown as a trend — never a verdict**.
  Say "projected risk", "elevated load", "deviating from baseline", "flag for
  review". Never "predicts injury", "X% chance of injury", "bone load", "tibial
  stress".
- Never a directional L/R claim ("left leg is weaker") — SPEC §5.5. `m5` copy
  speaks of imbalance magnitude only.
- Any alert/insight shows the evidence that fired it (context expander, §4).
- The detail view carries the standing one-liner — demo posture (2026-08-05):
  "Computed live from every impact and stride." (restore the SPEC §2 hedge
  "A monitoring aid, not a prediction." when the demo posture ends; keep
  consistent with §4). Plain language everywhere; the trainer is not technical.

## 12. Accessibility & responsive — SET

- Real `<button>`/`<a>` for every interactive element; visible `--accent` focus
  ring; tabs use `tablist`/`tab`/`tabpanel` + arrow keys; rename input:
  Enter/Esc; modals (if any) trap focus + Esc.
- Charts: table-view twins (§9); tooltips are native `title` attributes and are
  **not** surfaced on keyboard focus — ⚠️ tracked as outstanding a11y work (like
  the §6b calibrated-chip note); figure
  is a `<canvas aria-hidden>` inside a `role="img"` wrapper carrying the `aria-label`;
  live regions announce new alerts
  (`aria-live="polite"`).
- Contrast: ink tokens ≥4.5:1 on their surfaces; `--ink-3` used ≥11px only;
  status-on-surface ≥3:1 (validated).
- Breakpoints: ≤1024px sidebar → top bar; ≤1180px detail stacks (live first) and
  the hero figure hides; ≤680px panels single-column. The hero **never**
  auto-collapses — collapse is manual via the localStorage toggle (§3).

## 13. Data/API mapping — SET (frontend consumes BACKEND_SCHEMA §3 only)

| Widget | Source | Cadence |
|---|---|---|
| sidebar devices, panels registry, sensor rows, **battery** (`soc`) | `GET /api/devices` | 10 s poll + WS `status` events |
| calibration badge (§6a) | the tick's `cal` field (+ `cal_failed` from `f`) — no request of its own | 60Hz, repainted at 2 Hz |
| live sparkline/charts, current values, flags | `WS /ws/live` (+ `GET /api/metrics/recent` backfill) | 60Hz |
| projected-risk blocks, Projections tab | `GET /api/forecasts/latest` (the Projections tab also fetches `/api/metrics/history` for the actuals line) | 60 s poll |
| hero alerts tile ("Alerts · 30 min") | `GET /api/insights?limit=20` (unscoped), filtered to alert severity in the last 30 min | 30 s poll |
| History tab | `GET /api/metrics/history` (window ∈ `PAST_WINDOWS`) | 60 s poll |
| detail-page period labels, trend arrow, coverage chip | `GET /api/metrics/windows` | 60 s poll |
| insight chip on an Overview panel | `GET /api/insights?device&limit=5` (device-scoped) | 30 s poll (`POLL_INSIGHTS_MS`) |
| Insights tab (the advice timeline) | `GET /api/insights/timeline` **+** `GET /api/insights?device&limit=100` (evidence join on `(rule_id, created_at)`) | 10 s poll (`POLL_ADVICE_MS`) |
| rename | `PATCH /api/devices/:id` | on action |
| auth | `POST /api/auth/login|logout`, `GET /api/auth/me` | on action |

Evidence rendering (`lib/evidence.ts`) owns the expander contract: which `context` keys are
hidden, their display order, and the translation of jargon into trainer language (`z` renders as
"vs their normal range", `sd` as "their usual spread", quality/coverage as percentages). That is
a §11-copy-rules-level decision, so it lives in one module rather than in a component.

Frontend constants (`lib/config.ts`): `OFFLINE_HIDE_MS = 10_000`, `POLL_ADVICE_MS = 10_000`,
`HISTORY_MAX_BUCKETS = 30`, the live-chart set (`LIVE_BUFFER_S`, `BACKFILL_S`,
`RENDER_DELAY_S`), the WS set (`WS_BACKOFF_MIN_MS`/`MAX_MS`, `WS_CLOSE_UNAUTHORIZED`), poll intervals
above, metric map (`lib/metrics.ts` — SPEC §9 names/tooltips + §8 colors). Window
and horizon labels are always generated from config strings — never hardcoded.
