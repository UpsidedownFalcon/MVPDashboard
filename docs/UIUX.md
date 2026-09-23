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
| `/storage` | Sleeve storage | **added 2026-09-23** — plug a knee sleeve in over USB: edit its `CONFIG.TXT`, transfer and verify its log files (§15). Chrome/Edge on https or localhost only |

**Navigation is a left sidebar** (224px, `--surface` on `--bg`, hairline right border):
1. HIPPOS logo — the `<LogoFull />` inline SVG component (paths taken from
   `mockup/visual_guidelines/Logo/svg/`; see §10 for why it must be inline), links `/`.
2. "Unit overview" nav item under a "Command" section eyebrow (military theme 2026-09-12, STAGE4 R3),
   followed since 2026-09-23 by **"Sleeve storage"** (lucide `HardDrive`, route `/storage`, §15) in
   the same section; its label is `STORAGE_COPY.navItem`.
3. **Soldiers** section (2026-09-12; was "Athletes", and "Athletes online" before 2026-08-06): one row per **registered**
   device, online first then by name — display name, status dot (muted when offline),
   current composite as a small number tinted by risk band ("--" when offline).
   Offline soldiers stay listed with their stored data reachable; the empty state reads
   "no soldiers registered". Click →
   `/device/:id`. Active route gets a 2px `--accent` left rail + 5% accent wash
   (old-mockup pattern, kept).
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
- Middle: eyebrow + headline + short paragraph in plain language (§11 copy rules; since
  2026-09-12 the commander-readiness copy of STAGE4 Appendix B, which says "Built for units
  like the 1st Cavalry Division", never "used by"),
  then a 6-metric mini-legend: color swatch + display name + one-clause tooltip each
  (from `lib/metrics.ts`, SPEC §9 names).
- Right: live squad stats as stat tiles — "Soldiers online", highest projected risk
  (name + value, risk-band tinted; falls back to a "Highest risk now" tile when no
  forecasts exist yet), and "Alerts | 30 min" (alert-severity insights squad-wide
  in the last 30 minutes).
- **Collapsible**: an "About this data" toggle collapses the hero to a single slim
  row; state persists in `localStorage`. Daily users get density, first-time
  viewers get the story.

**Device panels** — one per **registered** device, online first (2026-08-06;
reverses the 2026-08-02 "silent >10 s disappears" rule). An offline device keeps
its card: offline badge, frozen sparkline, and its stored projections and top
insight stay browsable; the "Soldiers online" hero stat counts only the online
ones. New devices appear on first packet. Grid: 1–4 columns
responsive (auto-fill, 330px min), cards `--surface` radius 16, hover raises border to `--border-hover`;
the whole card is one click target → `/device/:id` (rename control excepted).

Panel contents, top to bottom:
1. Header row: editable name (✏ inline: click → input, Enter saves via
   `PATCH /api/devices/:id`, Esc cancels, optimistic + rollback), online badge,
   **calibration badge** (§6a) while settling, the static **sensor summary** line
   "4 sensors | 6400Hz logging" (STAGE4 R1, 2026-09-12: it replaces the quality meter and the
   four sensor micro-dots on the card, which are not reachable from the overview at all;
   hidden while the device is offline — **since 2026-09-23 a knee-sleeve rig words this line
   from its own shape instead**, §4), and the **battery** (§6b) top-right.
2. **Projected Injury Risk block — the panel's headline.** Closest configured
   horizon rendered as the stat-tile hero: label "Projected risk | +10m" (label
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
  "waiting for the first projection..." beneath. **No minute estimate**: the client is never
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
while settling, then the **sensor summary** (STAGE4 R1, 2026-09-12): a "»" toggle followed by
the static line "4 sensors | 6400Hz logging" (a deliberate literal, `SENSOR_SUMMARY_TEXT` in
`lib/config.ts`). Clicking the toggle swaps the static line, in place, for the quality meter + %
and the **per-limb sensor row** (for each mapped limb in sorted order — limb label, live rate
("641Hz"), liveness dot (§6); a limb that has never streamed shows its dot in critical, with
"never streamed" in the dot's tooltip), and the toggle flips to "«". The state is never
persisted: every detail-page mount opens collapsed (the page remounts it per `:id`), and the
whole summary is hidden while the device is offline. Active flag chips, then — for a
**knee-sleeve rig only** — the sleeve controls (below), and the **battery**
(§6b) top-right.

**Amended 2026-09-23 (user decision) — the sensor summary is rig-aware.** The STAGE4 R1 literal
was written when one wearable kind existed, and on a two-sensor knee sleeve it states a number
that is simply false. The rule now:

| Rig | Summary line |
|---|---|
| bilateral unit, demo soldier, or any device whose `kind` the API does not state | `4 sensors \| 6400Hz logging` — the R1 literal, **verbatim and unchanged** |
| one sleeve, leg set | `2 sensors \| one leg \| 6400Hz logging` |
| one sleeve, **leg cleared by an operator** (since 2026-09-23 a new sleeve arrives with the leg its `source_id` implies, so this is the cleared case — decision H, below) | `2 sensors \| side not set \| 6400Hz logging` |
| two sleeves paired | `4 sensors \| 2 sleeves \| 6400Hz logging` |

Every string lives in one exported table (`RIG_COPY` in `lib/rig.ts`) that the copy-rules test
walks, and `kind`/`units` are optional on the API type, so anything that does not carry them
reads as bilateral. This is a deliberate, recorded re-opening of STAGE4 R1 **for sleeves only**
(docs/tasks/STAGE4.md, as-built note).

**Sleeve controls (`RigControls`) — new 2026-09-23, device header, sleeve rigs only.** A
bilateral unit has fixed sides and compile-time full-scale constants and a demo soldier is
bilateral, so neither ever renders this. Per sleeve in the rig:

- **Leg** — a Left / Right segmented group. Unset reads "side not set"; the dashboard never
  guesses a side, and an unset sleeve is not assumed to be either leg.
  **Amended 2026-09-23 (PLAN_msd_management decision H):** the leg shown **defaults to the
  sleeve's own `source_id`** (0 = left, 1 = right) — the value the person fitting the sleeve
  wrote on its card, or set in Sleeve storage (§15) — which registration seeds into the unit, so
  a new sleeve arrives with a leg rather than "side not set". That is the fitter's own statement
  carried through, not the UI inferring anything. The operator may still override it here or
  **clear** it (`PATCH {"side": null}`); a cleared side reads "side not set", streams side-less
  limbs and dims both legs exactly as before.
- **Full scale** — a readout, `+-32 g | +-4000 dps`, that expands into two segmented groups over
  the allowed sets (accel 2/4/8/16/32 g, gyro 125/250/500/1000/2000/4000 dps). This is
  **configuration, not measurement**: the sleeve's datagrams carry no scale (TRD §3).
- **`Pair with...`** — expands an inline list of unpaired sleeves currently known, plus a leg
  choice for each side (the same inline-form pattern as the Override note). **`Unpair`** releases
  the member, which reappears as its own soldier with its own history.

Every one of those mutations **hard-resets the soldier's biomech session** on the backend, so
the stakes are higher than a rename: errors are shown **inline** rather than swallowed, and
success invalidates the registry, the unit list and the per-rig `windows`, `history`,
`forecasts`, `insights` and `advice-timeline` caches — for **both** rigs on a pair or unpair.
⚠️ **Accepted:** the 60 s live buffer still holds samples recorded before a full-scale change,
so the live charts show a step until they scroll past it.

**Left column — LIVE:**
- Top row: the humanoid figure (compact variant, §10) **driven by real data**:
  sensor dots lit by per-sensor liveness (from `/api/devices` sensors + tick flow),
  ping animation running only while the device streams; when `m5` is non-null the
  OTHER leg is dimmed to ~0.55 alpha — ambient de-emphasis matching the
  side label on the `m5` row, with nothing emphasised when the split reads
  "even" (updated 2026-08-03: BACKEND_SCHEMA §2 sanctions a
  **neutral** side readout; what SPEC §5.5 forbids is the *claim* — "weaker", a
  finding, or any cross-session comparison — not the factual side).
  **Amended 2026-09-23 for one-leg rigs:** when the rig maps only some limbs the
  figure **skips the sensor nodes of the limbs it does not have** and leaves
  those bones dim — a lone right-leg sleeve lights the right leg only, and a
  sleeve whose side an operator has cleared streams side-less limbs that match no
  bone, so **both** legs render dim (since 2026-09-23 a new sleeve arrives with the
  leg its `source_id` implies, so this is the cleared case — decision H, above). Load emphasis is suppressed unless **both** legs are
  instrumented: with one leg there is no left/right comparison to make, and
  tinting a leg anyway would read as a directional claim (SPEC §5.5). The
  figure's `aria-label` is derived from the rig's **actual sensor count**
  ("Soldier wearing 2 leg sensors streaming live motion data", singular at 1),
  never a fixed four and never a left/right wording.
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
   5. the **static coaching cue** (`tip`), under a **"PTI cue"** label (military theme
      2026-09-12; was "Coaching cue")
      (demo posture; was "General cue — not measured") — it is catalogue text,
      identical every firing;
   6. the **Evidence expander** (`<details>`) over the joined `context`;
   7. the **decision footer** (2026-08-07): undecided cards show two quiet
      buttons — **Adopt** (record the advice was used) and **Override**, which
      expands an inline optional text field ("What are you doing instead?") with
      Save/Cancel; blank saves as plain "overridden". A decided card replaces
      the buttons with its marker — green-tinted "✓ Adopted" or muted
      "↪ Overridden — *note*" — plus a small "change" affordance that reopens
      the buttons (decisions are changeable; the server keeps every press and
      the newest wins). The state rides `action.decision` on the timeline, so
      it survives reloads; a **re-fired** action is a new card and asks fresh.

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
   10% opacity); "Made <relative time> | <model_version from the response>" stamp in muted
   ink — **never a hardcoded version string**. Crosshair +
   tooltip; table-view toggle (horizon, prediction, CI). Empty: "First
   projection in a couple of minutes..." (§7).

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
| quality | % + 5-bar meter (since 2026-09-12 only inside the expanded sensor summary, §4): fill `--status-good` ≥90, `--status-warning` 60–90, `--status-critical` <60; track = same hue at 20% opacity |
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

A **"Stand still | Ns" countdown** chip appears on the overview card and the detail header
while a device is calibrating, then a brief verdict — **"Calibrated"** (the icon supplies
the tick; there is no ✓ character in the label) or **"Calibration failed"** — for ~8 s. Its
whole job is to tell the soldier *how much longer to stand still*, and whether it worked.

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
only on a **real absence** — silent longer than `OFFLINE_HIDE_MS` (10 s), whose return counts
as a new session. (Since 2026-08-06 offline devices stay visible in the UI, so this threshold
is purely the badge's re-arm rule — it no longer hides anything.) It deliberately does
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
leg MCUs** — a flat unit must not hide behind a healthy one. The tooltip names that source, and
**since 2026-09-23 it is rig-aware**: "lowest of the two leg sensors" for a bilateral unit,
"sleeve" for one knee sleeve, "lowest of the two sleeves" for a paired rig — the reading means
something different in each case, and a single sleeve has nothing to be the lowest of. Amber ≤20%, red + a slow pulse
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
| `one_leg` | **added 2026-09-23**: the rig instruments one leg only (a single knee sleeve, whether its leg is set or an operator cleared it). Chip label "one leg", hint "One leg instrumented - balance needs both legs". Structural, **not** a fault — `m1`..`m4` are unaffected and only `m5` is impossible, so it must read calmer than `degraded_sensors`, never as an alert | muted |
| `unvalidated` | **not rendered** (demo posture 2026-08-05, `HIDDEN_FLAGS` in `metrics.ts`); still on the wire per SPEC §11.1 | — |

`warming_up` and `degraded_sensors` must never look alike: muted grey chip + greyed
panel vs alert chip + explicit "no data from <limb>". **The same holds for `one_leg`
(2026-09-23):** on a one-leg rig the `m5` panel is greyed with the muted "one leg" reason, and
`one_leg` outranks every other reason **for `m5` only** — `m1`..`m4` keep their own reasons, so
`m4` still reads "warming up" while it learns this soldier's baseline. The `m5` reason order is
`one_leg`, `degraded_sensors`, `partial`, `saturated`, `warming_up`. The uncalibrated→calibrated
transition is a visible step in `m4`/`m5`: when `uncalibrated`/`carried_over` clears,
show a muted "calibrated ✓" chip for ~10 s — a system event, not a change in the
athlete. ⚠️ **NOT YET IMPLEMENTED** — `FlagChips` renders only flags that are currently
present; nothing tracks the clear transition. Tracked as outstanding UI work.
Original rationale: the step change is in the
athlete (SPEC §3.8).

## 7. Empty & error states — SET

- No devices **registered** (offline ones stay on the grid since 2026-08-06):
  hero stays; panel area shows "Waiting for soldiers... point wearables at this
  server's UDP port."
- No insights: "Nothing to flag right now" + "Advice appears here within about a
  minute of something worth acting on."
- History without enough data: "collecting... no data yet in past X"; partial coverage shows a
  "partial | N% of window" chip beside the period selector.
- Projections tab before the first run: "First projection in a couple of minutes..."
  — the tab may hint at scale; the **overview card** still shows no minute
  estimate (the client is never told `PREDICT_INTERVAL_S`).
- API errors: inline per-panel "...retrying..." notices — no toast exists, and there
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
- **Military vocabulary and plain ASCII punctuation** (2026-09-12, STAGE4 R3/R4): every
  user-facing string says "soldier", never "athlete"; the advice tip label is "PTI cue"; no em
  dash, en dash, ellipsis or middle dot reaches the UI — a spaced hyphen joins clauses, "--"
  marks a missing value and " | " is the separator ("Injury risk | now", "Alerts | 30 min").
  The backend's own advice text is deliberately untouched, so a real device's cards keep the
  backend wording; demo soldiers (§14) are worded on the frontend.
- **Never state a sensor count the rig does not have** (2026-09-23). The hero strip no longer
  opens with a count at all: the eyebrow reads `Lower-limb telemetry | thigh and shin sensors |
  live` and the paragraph opens "Sensors on each soldier's thighs and shins stream motion
  hundreds of times a second" (both were "4 sensors" / "Four sensors" until sleeves existed, and a squad can now mix
  two-sensor and four-sensor rigs). Per-soldier lines say what **that** rig carries (§4), and
  the STAGE4 R1 literal survives verbatim wherever the rig really is a four-sensor bilateral
  unit — including every demo soldier.
- **Never claim a leg the operator has not set** (2026-09-23). An unassigned sleeve reads
  `side not set`; the UI does not infer left or right from the wire, does not tint a leg on a
  one-leg rig, and `one_leg` copy says what is missing ("balance needs both legs") rather than
  anything about the soldier — the SPEC §5.5 ban on directional claims applies with full force
  here, where exactly one leg is being measured.
  **Amended 2026-09-23 (PLAN_msd_management decision H):** the leg comes from the sleeve's own
  `source_id` — set by the person fitting it, on the card or in Sleeve storage (§15), and seeded
  into the unit at registration — so a new sleeve shows Left or Right from its first packet.
  That is the fitter's statement carried through, not the UI inferring; the operator may
  override or clear it, and a cleared side still reads `side not set` and lights neither leg.
  Everything else in this rule stands: no leg tint on a one-leg rig, and `one_leg` copy names
  what is missing.

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
- Sleeve storage (§15): validation and failure lines are `role="alert"`; the eject notice,
  "Reading the sleeve...", the do-not-unplug line and the run summary are `role="status"`;
  progress and per-file status are `aria-live="polite"`; the Advanced toggle carries
  `aria-expanded`/`aria-controls`; segmented groups are `role="group"` with `aria-pressed`
  buttons; every input has a `<label for>` and, where help text exists, `aria-describedby`.
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
| Adopt/Override buttons on a card | `POST /api/insights/decisions`, then invalidate the timeline query | on press |
| rename | `PATCH /api/devices/:id` | on action |
| **sensor summary wording, battery tooltip, instrumented legs, sleeve controls** (2026-09-23) | `kind`, `units[]` and `sensors[].unit_id` on the **same** `GET /api/devices` — no extra request | with the registry poll |
| **the pair picker's list of sleeves** | `GET /api/units` | when `Pair with...` is opened |
| **leg / full-scale change, pair, unpair** | `PATCH /api/units/:id`, `POST /api/units/:host/pair`, `POST /api/units/:id/unpair`, then invalidate `devices`, `units` and both rigs' `windows` / `history` / `forecasts` / `insights` / `advice-timeline` | on action |
| auth | `POST /api/auth/login|logout`, `GET /api/auth/me` | on action |
| **Sleeve storage: "Streams to ... (this dashboard)" line and `Point at this dashboard`** (2026-09-23, §15) | `GET /api/config/udp-target` | on page open, re-fetched when older than `STORAGE_UDP_TARGET_STALE_MS` (60 s) |
| **Sleeve storage: soldier name on the identity line, duplicate-number warning** | `GET /api/units` + the merged device list | on page open, with the registry poll |
| **Sleeve storage: full-scale sync after a save** (decision I) | `PATCH /api/units/:id` `{accel_fs_g?, gyro_fs_dps?}`, then invalidate `devices`, `units` and that rig's `windows` / `history` / `forecasts` / `insights` / `advice-timeline` | after a save that changed the card's full scale, only when that unit is already known |
| **Sleeve storage: the drive and the destination folder** | the browser's File System Access API — **never the network**; handles remembered in IndexedDB | on click |

Evidence rendering (`lib/evidence.ts`) owns the expander contract: which `context` keys are
hidden, their display order, and the translation of jargon into trainer language (`z` renders as
"vs their normal range", `sd` as "their usual spread", quality/coverage as percentages). That is
a §11-copy-rules-level decision, so it lives in one module rather than in a component.

Frontend constants (`lib/config.ts`): `OFFLINE_HIDE_MS = 10_000` (since 2026-08-06 only the
calibration badge's re-arm threshold — it hides nothing), `POLL_ADVICE_MS = 10_000`,
`HISTORY_MAX_BUCKETS = 30`, the live-chart set (`LIVE_BUFFER_S`, `BACKFILL_S`,
`RENDER_DELAY_S`), the WS set (`WS_BACKOFF_MIN_MS`/`MAX_MS`, `WS_CLOSE_UNAUTHORIZED`), poll intervals
above, metric map (`lib/metrics.ts` — SPEC §9 names/tooltips + §8 colors). Window
and horizon labels are always generated from config strings — never hardcoded.
Added 2026-09-23: `SENSOR_SUMMARY_TEXT` (the R1 literal) is joined by
`LOGGING_RATE_TEXT = '6400Hz logging'`, which the sleeve variants are built from, and by
`ACCEL_FS_ALLOWED_G` / `GYRO_FS_ALLOWED_DPS` (the segmented groups' options, mirroring the
backend's allowed sets). All rig-dependent user-facing strings live in `RIG_COPY`
(`lib/rig.ts`), which the copy-rules test walks.
Added 2026-09-23 (sleeve storage, §15): `STORAGE_READ_CHUNK_BYTES` (4 MiB card reads),
`STORAGE_RATE_EWMA_ALPHA` (0.2), `STORAGE_EXPECTED_BYTES_PER_S` (1 MB/s, the first ETA),
`STORAGE_UDP_TARGET_STALE_MS` (60 s), `STORAGE_TXT_CFG_PROBE_BYTES` (4096) and
`STORAGE_BAD_BLOCK_CONFIRM_MAX` (32); the Advanced editor reuses `ACCEL_FS_ALLOWED_G` /
`GYRO_FS_ALLOWED_DPS`. Firmware limits (key ranges, byte limits, the 159-byte line) are
**format facts** in `lib/storage/configSchema.ts`, deliberately not tunables. Every string of
the page lives in `STORAGE_COPY` (`lib/storage/copy.ts`), also walked by the copy-rules test.

## 14. Demo soldiers — SET (2026-09-12, STAGE4 R2)

Five synthetic soldiers are **always** present alongside any real devices, including when
nothing has ever registered (so the "Waiting for soldiers" empty state is unreachable in
practice). They are indistinguishable from real devices in every view — no chip, tooltip or
label marks them (user decision 2026-09-12, risk accepted) — except that their ids are visibly
synthetic in the address bar: `/device/demo-1` … `/device/demo-5`.

- **Roster and stories** are data in `frontend/src/lib/demo/profiles.ts`: US Army rank +
  surname (SGT Alvarez, CPL Nguyen, PFC Okafor, SSG Brooks, SPC Ramirez), per-series
  keyframe envelopes over session seconds, battery start/drain, scripted flag chips
  (`carried_over` on CPL Nguyen, `warming_up` on SPC Ramirez) and advice *episodes*.
  Changing a story is a table edit. The session start is captured once per page load as
  "2 h 05 min ago", so every reload shows the same picture and a tab left open never jumps.
- **Injection seams, never components**: `useMergedDevices` appends them after the real
  registry and `useVisibleDevices` sorts real devices first; `LiveProvider` feeds their
  60 Hz ring buffers from a wall-clock generator in a separate ref (immune to the
  reconnect/tab-return wipe and the REST backfill) and folds them into the 250 ms snapshot;
  every `lib/api.ts` helper answers a `demo-` id from `lib/demo` without a request. The
  network never learns a soldier exists.
- **Everything a live device has**: all six live series (m4/m5 never blank), sidebar risk,
  hero stats (they count as online, can be the named "Highest projected", and their alerts
  feed "Alerts | 30 min"), card projection + top insight, History (`5m,30m,2h` hard-coded),
  Projections (`10m,30m,1h`, "Made … | trend-ols-1"), and the Insights timeline across
  live / past 5m / 30m / 2h with reasons, PTI cue and Evidence. Advice comes from a
  TypeScript port of the backend rule catalogue and `group_actions()`
  (`lib/demo/insights.ts`): episodes only say when a rule is evaluated, a row is emitted only
  when the rule's precondition holds on the same signal the charts draw, so evidence numbers,
  reason sentences and severity always agree with the screen.
- **Controls look live, change nothing**: rename snaps back to the scripted name (no cache
  write, no PATCH); Adopt / Override close their form and the card stays undecided (no POST).
  No calibration badge ever arms (`cal` is always null).
- **Demo soldiers stay bilateral** (2026-09-23, user decision L): they carry no `kind` or
  `units`, so they read as four-sensor bilateral rigs everywhere, keep the STAGE4 R1 summary
  literal verbatim, and never render the sleeve controls. The four unit helpers short-circuit on
  a `demo-` id exactly like the others, so the network still never learns a demo soldier exists.
- Unit tests (`npm test`, vitest) pin determinism, ranges, exact buffer windows, the
  timeline/event-log join, backend parity of the grouping, the scripted stories, and zero
  fetch calls for demo ids. See `docs/tasks/STAGE4.md` for the plan and as-built notes.

## 15. Sleeve storage — SET (2026-09-23, PLAN_msd_management change-set 1)

Route `/storage`, sidebar item "Sleeve storage" under Command (§1). The page opens a plugged-in
knee sleeve's **HIPPOSDATA** USB drive in the browser and does two things: it edits the sleeve's
`CONFIG.TXT` exactly the way the firmware will parse it, and it moves the sleeve's `LOG_NNNN`
files to a folder on this PC, verifying every copy before the original is deleted. It is
**browser-native**: the File System Access API reads and writes the drive, nothing is uploaded
and there is no helper app (decision A). Change-set 2 — a CSV and a plain-text summary per
transferred log, produced in a Web Worker — is **planned, not built** (PLAN_msd_management §5);
until then the page ends at the verified raw copy.

**Browser requirement.** `isSupported()` is "`showDirectoryPicker` exists **and** the context is
secure", i.e. Chrome or Edge on `https://DOMAIN` or `localhost`. Anything else (Firefox, Safari,
a plain-`http` LAN address) renders the intro line plus one `role="alert"` notice — "Sleeve
storage needs Chrome or Edge on a secure (https) address. This browser or address cannot open
drives." — and nothing else. Both directory handles (sleeve, destination) are persisted in
IndexedDB (`hippos-storage/handles`); on the next visit a handle whose permission is still
granted opens by itself, otherwise a **Reconnect** button asks for it again (`requestPermission`
needs a click, so it never runs on mount). Where the browser offers "Allow on every visit"
(Chrome 122+, a browser behaviour recorded in the plan of record, not something the page
controls) reconnecting becomes automatic. IndexedDB being unavailable (a private window) simply
means the user picks again.

**Layout** — one column, title "Sleeve storage", four cards top to bottom; cards 2–4 exist only
once a drive is open and valid:

1. **Drive** — the intro line, `Open sleeve drive` (primary, `HardDrive` icon), `Reconnect sleeve
   drive` when a stored handle needs permission again, "Drive: {name}" once open, and "Reading
   the sleeve..." (`role="status"`) while validating. A picked folder is the drive **only if it
   holds `CONFIG.TXT`** (case-insensitive; the card's own spelling is kept for the write) —
   otherwise `role="alert"`: "That folder has no CONFIG.TXT. Pick the HIPPOSDATA drive itself,
   not a folder inside it." The other invalid states are read-failed (with the browser's detail)
   and permission-denied ("Try again and choose Allow"). The page never relies on `handle.name`
   to identify a drive. Open and Reconnect are locked while a save or transfer runs.
2. **Sleeve settings** — the `CONFIG.TXT` editor (decisions E, F, G). Identity line "Sleeve
   **u<dev>-<src>** | <soldier name>", the name coming from the merged device list (the unit's
   own rig, or the host rig it is paired into; otherwise "not yet seen by the dashboard"). Then
   any **file notices**, each with a warning icon: a byte-order mark ("...makes the sleeve ignore
   its first setting") with a `Repair file` button that queues the strip for the next save; a
   duplicated key ("the last value is the one the sleeve uses"); a leading-zero number ("reads
   as {n} to the sleeve (a leading zero means octal). Save to write it as plain decimal") or one
   the firmware cannot read at all ("...so it uses its built-in default. Enter a value to fix
   it" — Save stays blocked until it is replaced); a line longer than the firmware reads in one
   go. **Basic fields**: WiFi network (cannot be empty — the firmware would silently keep its
   factory network), WiFi password (masked, `Show`/`Hide` toggle with `aria-pressed`; empty is
   allowed with a warning), Sleeve number (0–255; "A sleeve with this number and leg is already
   known to the dashboard. If that is this sleeve, ignore this." when the draft identity already
   exists in `/api/units`), **Leg this sleeve is worn on** (a Left/Right segmented group,
   `role="group"` + `aria-pressed`, writing `source_id` 0/1), and two checkboxes (diagnostics
   log, stream over WiFi). **UDP row**: "Streams to {ip}:{port}" plus "(this dashboard)",
   "(not this dashboard)" or "(target unknown)" from `GET /api/config/udp-target`, and `Point at
   this dashboard`, disabled when already pointing here or when the api could not resolve its
   own address — then the reason shows: "The dashboard could not work out its own address. Set
   UDP_PUBLIC_IP on the server." **Advanced settings** is a toggle button (`aria-expanded`,
   `aria-controls`); opening it shows the warning ("Changing these can make the sleeve
   misbehave: stop logging, mis-scale its sensors, drain or over-protect its battery, or stop it
   streaming. Only change them if an engineer asked you to.") and an `I understand` checkbox that
   unlocks its fieldset **for this page session only** — `udp_ip`, `udp_port` (plus the same
   Point button), low-battery stop, WiFi transmit power (steps of 0.25 dBm), accelerometer and
   gyroscope full scale as segmented groups over the allowed sets, and the two battery
   calibration values (0 = off). Validation mirrors the firmware: ranges, allowed sets, UTF-8
   byte limits (ssid 32, password 64, `udp_ip` 15 — "a character outside plain ASCII counts as 2
   to 4"), no leading or trailing space (the firmware trims it), a 159-byte line limit; a problem
   renders inline with `role="alert"` and `aria-invalid` on the input. `Save to sleeve` is
   enabled only while something is pending (a changed value, a queued BOM repair or an octal
   rewrite), nothing is invalid, the drive is ready and no transfer runs; it reads "Writing..."
   during the save.
3. **Log files on the sleeve** — a `data-table` with Select | File | Size | Session | Firmware |
   Sleeve | Full scale | Status, grouped by session ascending, **all selected by default**, a
   select-all box (`aria-label` "Select all log files") and one box per row labelled by its file
   name. Only names matching `LOG_NNNN.BIN` / `LOG_NNNN.TXT` are ever listed; `CONFIG.TXT` cannot
   match that pattern, and `*.crswap`, `System Volume Information`, `$RECYCLE.BIN` and dotfiles
   are hidden. Firmware, sleeve (`u<dev>-<src>`) and full scale come from each BIN's 512 B
   header; a header that fails reads as a warning chip in the Sleeve column ("not a sleeve log",
   "unknown log format", "header checksum wrong", "header too short", "header could not be
   read") and the row stays transferable. The Status cell (`aria-live="polite"`) carries the
   per-file line during and after a run: Queued, "Copying {pct}% | {rate} | {eta} left",
   Verifying copy, Removing from sleeve, "Copied and removed from sleeve", "Copied (kept on
   sleeve)", "Already transferred", "Failed: {reason}", Cancelled. Selection is frozen while a
   transfer runs. Empty state: "No log files on this sleeve."
4. **Transfer** — `Choose destination folder` / `Reconnect destination folder`, "Destination:
   {name}", the hint "Each file goes to sleeve-uN-M/raw inside the destination, named after the
   sleeve number and leg recorded in that file", the `Keep copies on the sleeve (do not delete
   after transfer)` checkbox (**off** by default — decision J), and `Transfer selected` (enabled
   when a drive and a destination are ready, at least one listed file is selected, and no save
   or transfer runs), which becomes `Cancel` / "Cancelling..." while running, beside the
   persistent "Do not unplug the sleeve while a transfer is running." (`role="status"`).
   Progress: a `<progress>` bar labelled "Transfer progress" and "{done} of {total} | {rate} |
   {eta} left" inside an `aria-live="polite"` region (the rate is an EWMA,
   `STORAGE_RATE_EWMA_ALPHA`; the first ETA assumes `STORAGE_EXPECTED_BYTES_PER_S` = 1 MB/s).
   Afterwards: "Done: {copied} copied, {already} already transferred, {failed} failed,
   {deleted} removed from the sleeve." (plus "{n} not started." after a cancel), or
   `role="alert"` "Transfer stopped unexpectedly - {detail}". The speed note "Sleeves transfer
   at about 1 MB/s over USB, so a 512 MB file takes about 9 minutes." is always visible.

**Save flow** (`pages/Storage.tsx` → `lib/storage/configFile.ts`). Every value the editor shows
is what the **firmware** would read from the file (`interpretAsFirmware`: 159-byte `fgets`
pieces, C `isspace` trimming, whole-line `#`/`;` comments only, first-`=` split, last duplicate
wins, unknown keys ignored, base-0 integers), never a naive parse. `applyEdits` rewrites **only
the value span of the last line holding each edited key** and appends `key=value` with CRLF for
keys the file lacks — comments, unknown keys, line endings and even invalid UTF-8 elsewhere
survive byte for byte; it never writes a BOM or an inline comment, and integers are always plain
decimal (`010` would be octal to the firmware). The write goes through `createWritable` (a
`CONFIG.TXT.crswap` is visible beside the file until the browser swaps it in), the file is
**read back** and `verifyReadback` must find every intended value; otherwise `role="alert"`
"The file read back differently from what was written; nothing else was changed. Try again."
Success shows the **eject notice** (`role="status"`): "Saved. Now eject the HIPPOSDATA drive,
then unplug the cable." / "The sleeve re-reads its settings about 2 seconds after the cable is
out and starts a new session." — a host eject alone does **not** end the sleeve's session, so
the order is always eject, then unplug — plus, when they apply: "WiFi network or password
changed: also switch the sleeve off and on again." (`wifi_ssid` and `wifi_password` are the
only boot-effect keys), "Dashboard full scale for {unit} updated to match." (decision I: after a
save that changed `accel_fs_g`/`gyro_fs_dps` the page `PATCH`es `/api/units/{unit}` when that
unit is already known — never for an unknown or malformed id — and invalidates `units`,
`devices` and the rig's per-rig queries; a failure reads "...could not be updated - {detail}.
Set it on the soldier's page." as an alert), and "This sleeve will now appear as a new soldier
({to}). Pairing, leg and history stay with {from}." when the sleeve number or leg changed.

**Transfer guarantees** (`lib/storage/transfer.ts`, decisions J and K) — per file, in order:
**probe** (the destination folder `sleeve-u<dev>-<src>/raw/` is created — the identity is the
file's own header, else the TXT's `# cfg:` line, else `CONFIG.TXT`; a same-name file already
there is sized and CRC'd) → **copy** (4 MiB slices, each read awaited before the next, with a
running CRC32 and a whole-file block scan on the way; the sink must `close()` cleanly) →
**verify** (the **local copy is re-read**: length, CRC32 and an identical block-scan result are
required; every block the scan called bad is re-read from the card and compared byte for byte,
at most `STORAGE_BAD_BLOCK_CONFIRM_MAX` = 32 of them, so a transient USB read error can never
pass as on-card corruption and cost the only good copy; a `.TXT` is byte-compared whole) →
**dedupe** (a pre-existing local file with the same size and CRC means the fresh copy is
dropped and the row reads "Already transferred"; a different one keeps the fresh copy as
`LOG_0010-2.BIN`) → **delete** from the sleeve, **only** when copy and verify passed, "Keep
copies" is off and the run was not cancelled. A verify mismatch removes the local copy and
leaves the card untouched; a delete failure reports "the copy is good but the file could not be
removed from the sleeve"; a failure in any phase never deletes. Cancel or an unplug mid-copy
aborts the writable (Chrome discards the `.crswap`), so **no partial copy exists and the card is
as it was**; the listing is re-read from the card after every run. Failure reasons are plain
sentences ("the sleeve stopped answering - was it unplugged?", "could not write to the
destination folder", ...). The page never deletes anything on the PC. The engine runs on the
main thread (its CPU cost is negligible against the ~1 MB/s full-speed USB link) and touches no
DOM, so it can move to a worker for CS2.

**Never possible.** `CONFIG.TXT` cannot be listed, transferred or deleted (the transfer allowlist
is the `LOG_NNNN` pattern, pinned by `logNames.test.ts`); the destination is never deleted from;
the card is never written except by Save (`CONFIG.TXT`) and the post-verify delete; a demo id
never reaches the API (the one PATCH is guarded by `UNIT_ID_RE`).

**Copy rules.** Every user-facing string lives in `STORAGE_COPY` (`lib/storage/copy.ts`), which
`text.test.ts` walks for the §11 rules: plain ASCII (a spaced hyphen joins clauses, " | "
separates), "soldier" never "athlete", the device is a "sleeve" and its side is a "leg" ("Leg
this sleeve is worn on"; "Pairing, leg and history stay with..."). Firmware key names
(`wifi_ssid`, `accel_fs_g`, ...) appear only in notices that describe the sleeve's own file.
Composite messages are templates with `{slot}` holes filled at render time, so the test sees
every word the page can show.

**Accessibility.** Real buttons everywhere (segmented groups are buttons with `aria-pressed`);
each input has a `<label for>` and, where a help line exists, `aria-describedby`; validation and
failure lines are `role="alert"`; the eject notice, "Reading the sleeve...", the do-not-unplug
line and the run summary are `role="status"`; progress and per-row status are
`aria-live="polite"`; the Advanced toggle carries `aria-expanded`/`aria-controls`; the whole
basic fieldset is `disabled` while a save or transfer runs, so nothing can be edited mid-write.

**Without hardware.** Pick any local folder holding a copy of `CONFIG.TXT` and some
`LOG_NNNN.{BIN,TXT}` files; the page treats it as the drive. The real-drive checks — the picker
offering the drive root, "Allow on every visit", the `.crswap` visible during a write, unplug
mid-copy leaving the card intact — are a manual checklist, not headless tests
(PLAN_msd_management §4.6).
