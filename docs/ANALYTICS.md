# Analytics — historical windows, forecasts, insights

| | |
|---|---|
| Status | Implemented 2026-08-03. Supersedes the S2-T03/T04/T05 starter behaviour where noted. |
| Scope | `backend/api/queries.py` (windows), `backend/api/jobs/predict.py` (forecasts), `backend/api/jobs/insights.py` (rules). |
| Related | [biomech/SPEC.md](biomech/SPEC.md) §6 (the composite) · [TRD.md](TRD.md) §5 · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) §5 |
| Constraint | Equation-level changes only — no schema migrations, no new API fields, no new response states. §5 lists what that excluded and what it costs. |

---

## 1. The composite is `acute` alone — the dose floor was removed

Until 2026-08-03 the composite was `floor + (100 − floor)·acute/100` with `floor = 0.50·m3`,
which made it **exactly separable** into a slow dose term and a fast activity term:

```
1 − composite/100  =  (1 − 0.005·m3) · (1 − acute/100)
```

That identity is what the forecast was built on. **It no longer holds.** A 13-minute worn
protocol showed the composite spanning only 15.5–18.5 across everything from standing still to
squats to failure, because ~16 of those points *were* the floor and `acute` contributed 0.33.
Dose now reduces **capacity** instead (biomech SPEC §6.1a), so:

```
composite = 200·r^3.5/(r^3.5 + 1),   r = demand / capacity
capacity  = 100 − 55·(0.50·m3 + 0.30·m4 + 0.20·|m5|)/Σweights
```

Standing still gives demand 0 and therefore **risk 0**, whatever the accumulated load; fatigue
shows up as a steeper response the moment the athlete moves again.

## 2. Forecasts — `trend-ols-1`

The two-component dose-scenario model was **retired** with the floor. Under the new composite
"if they stop now" is trivially 0, so the scenario band carries no information — publishing it
would be false precision.

`fit()` is now a plain trend projection on the composite with a **correct OLS prediction
interval**, `t₍.₉₇₅,n−2₎·σ̂·√(1 + 1/n + (x*−x̄)²/Sxx)`. The old `1.96·σ̂·√(1 + h/span)` had a
leverage term linear in `h` where the truth is quadratic and no dependence on `n` at all, making
it ~38% too narrow at `n = 10` before autocorrelation.

Retained from the previous model: `target_time` is anchored on the **last observed bucket**, not
`made_at`; and a device staler than `SESSION_GAP_S` is not forecast at all.

⚠️ **Open — the natural successor.** Project **capacity**, which is knowable (dose decays at a
known, now intensity-dependent rate), against **demand** persisted from recent behaviour, which
is not. That is the honest decomposition under the new composite. It is recorded rather than
guessed at.

### 2.1 Time to first forecast — the bootstrap path

**The problem.** The steady model trains on `metrics_1m` and needs `MIN_BUCKETS = 10` one-minute
buckets. That aggregate is materialized-only with `end_offset` 1 min and `schedule_interval`
1 min, so its newest bucket lands 1–2 min late, and the job then ran only every 5 min. Observed
on the VPS: **no projection at 5 min, none at 10 min, first one 15–20 min in.**

| Stage | Cost |
|---|---|
| 10 × 1-minute buckets must exist | ~10 min |
| cagg `end_offset` + `schedule_interval` | +1–2 min |
| `PREDICT_INTERVAL_S = 300`, first run 5 min after API start | +0–5 min |

**The fix.** A **bootstrap path** fits the *same* model with the *same* prediction interval to
sub-minute buckets read straight off the raw hypertable — no materialization lag, and no
1-minute floor on bucket width. `PREDICT_INTERVAL_S` also drops to 60.

Measured (`test_bootstrap_forecasts_minutes_after_first_data`, and directly on a scratch DB
2026-08-04):

| Streaming time | Bootstrap buckets | Span | Forecast? | Horizons |
|---|---|---|---|---|
| 2.00 min | 8 | 1m45s | no | — |
| 2.25 min | 9 | 2m00s | no | — |
| **2.50 min** | **10** | **2m15s** | **yes** | `1m`, `2m` |
| 3.00 min | 12 | 2m45s | yes | `1m`, `2m` |

So the floor is **2.5 min of streaming**, plus up to one 60 s job interval → **first projection
at 2.5–3.5 min**.

**Horizons are capped by the observed span.** OLS extrapolation error grows with the *square* of
the distance from the sample mean — the `(x*−x̄)²/Sxx` leverage term. Projecting an hour ahead
from two minutes of data is not a forecast, it is a straight line with an error bar wide enough
to be uninformative, and publishing it would be false precision under SPEC §2. `_capped_horizons`
therefore publishes a horizon only while `h ≤ observed span`, which is why the earliest
projections are deliberately short ones.

**It is a warm-up, not a replacement.** As soon as `metrics_1m` holds `MIN_BUCKETS` for a device,
that device returns to the steady path with the configured `FUTURE_HORIZONS` — steady-state
behaviour is unchanged. Bootstrap rows carry `model_version = "trend-ols-boot-1"` and
`/api/forecasts/latest` sets `provisional: true`, so the UI can say the projection is early
rather than presenting it as established. The bootstrap query only runs for devices the
aggregate cannot yet serve; once all devices are established it is skipped entirely.

## 3. Windows

- **Bucket means are weighted by row count.** A `metrics_1m` row is a 1-minute bucket
  holding `n` 60 Hz samples, so an unweighted `avg()` of per-bucket averages gave a
  2-second partial bucket the same weight as a full one. Measured on the 712 real rows in
  the local database (buckets `n` = 300 and 412): **unweighted 13.688 vs weighted 12.348,
  an 11% error.** `n` was already being selected and discarded.
  Applied to `composite`, `quality` and `m3` only — those three are never null, so `n` is
  the exactly correct weight. `m1`/`m2`/`m4`/`m5` stay unweighted: each bucket's value is
  itself a null-skipping average over an unknown subset of that minute, so `n` is the
  *wrong* weight and the right one (`count(mN)` per bucket) is not a column the aggregate
  has. See §5.
- **The trend dead-band scales with dispersion.** The fixed ±2.0 was ~2.4% of an 85
  reading but 20% of a 10 reading, applied identically to a 2 m and a 2 h window. It is
  now `max(2.0, 0.5·pooled_sd)`. The factor multiplies the **standard deviation, not the
  standard error**: composite samples are strongly autocorrelated, so the effective sample
  size behind a window mean can be O(1) and `sd/√n` would overstate precision by an
  unknown factor.
- **The shortest window reads the raw hypertable.** `use_raw` compared `td < 5m`, and the
  shipped `PAST_WINDOWS` starts at exactly `5m`, so the raw path was dead code in
  production — while `metrics_1m` is materialized-only, hiding its newest 1–2 minutes
  (up to 40% of a 5 m window). Now `td <= 5m`.

## 4. Insights — the rule catalogue

Every insight carries an **`action`** (short imperative, rendered as the card headline) and a
**`rationale`** (the why, with the measured numbers), plus `context` holding the evidence that
fired it. `message` remains as a standalone summary for anything that cannot render the pair.
Migration 003 adds **`action_id`** (catalogue key several rules deliberately share) and
**`reason`** (one short sentence with the numbers, sized to render as a bullet with no
expander) — see §4.6 and §4.7.

### 4.1 Why these rules survive a future metric retune

Rules about the athlete fire on `MetricView.z` — the deviation of the recent window from that
athlete's **own** longer-run baseline, expressed in units of that athlete's **own** spread:

```
z = (value_short − value_baseline) / max(sd_baseline, SD_FLOOR)
```

This is **exactly invariant** under any affine rescale of a metric, which is what a change to a
biomech normalisation bound produces. Moving `lo` shifts every value by a constant (cancels in
the numerator); changing `hi/lo` scales value and `sd` by the same factor (cancels in the ratio).
So retuning `M1_LO`, `M3_HI`, the acute curve or the dose law changes the numbers an insight
*reports* without changing *whether it fires*. `test_z_is_invariant_to_a_metric_rescale` asserts
this over a range of gains and offsets.

Absolute thresholds have no such property, so they are used only where the scale is definitional
(the 0–1 `quality` ratio) or set by the operator (`INSIGHT_WARN/ALERT_THRESHOLD`).

### 4.2 Holistic by construction

A rule reads `ctx.metrics` — all five primitives *and* the composite, each with its value in the
shortest window, its baseline in the longest, its spread, its `z` and its trend — plus
`ctx.horizons`, every projected horizon with its prediction interval. Rules are not permitted to
depend on one number: `load_spike` reports what the projection says about where it is heading,
and `accumulated_load` combines the dose's past trend with the future residual.

### 4.3 The rules

Headlines below are the `ACTIONS` catalogue entries (§4.7 is the authoritative table — these two
must not drift apart again; the previous copy here still carried the pre-2026-08-04 wording).

| Rule | Severity | Fires on | Time axis | Action (`action_id`) |
|---|---|---|---|---|
| `load_spike` | warning → alert at 3 sd | composite ≥ 2 sd above own baseline | past + present + future | *Drop the next block down one level* (`ease_off`) |
| `accumulated_load` | warning → alert | `m3` ≥ 2 sd above own baseline **and** trending up | past + present + future | *No more hard sets — easy work only* (`cap_session`) |
| `residual_load` | warning | forecast `ci_low` at the furthest horizon ≥ warn threshold | future | *Leave a longer gap before the next hard block* (`plan_recovery`) |
| `impact_deviation` | info | `m1` or `m2` ≥ 2 sd above own baseline | past + present | **routes by metric**: m1 → *Lower the landing height or cut the reps* (`lower_landings`); m2 → *Soften the landings — check the surface* (`soften_landings`) |
| `movement_quality` | info, `unvalidated` (evidence marker only) | `m4` or `m5` ≥ 2 sd above own baseline | past + present | *Coach technique on the next block* (`flag_review`) |
| `composite_high` | warning → alert | live-window mean ≥ configured threshold | present | *Drop the next block down one level* (`ease_off`) |
| `rising_risk` | warning | mid-window trend up **and** a projection crossing alert | past + future | *Leave a longer gap before the next hard block* (`plan_recovery`) |
| `data_quality` | info | live-window quality < 0.8 × own baseline | data health | **event-log only** — no action card (demo posture 2026-08-05); `group_actions` skips its rows by `rule_id` |

> **Demo posture (2026-08-05).** The dashboard is a concept demo until sports-scientist-designed
> models land, so all *rendered* hedging was removed: the unvalidated caveat sentences no longer
> appear in rationale/reason text (the `ev["unvalidated"]` evidence marker and the SPEC §11.1
> doctrine below remain, for when validation exists), and sensor/hardware maintenance
> (`check_sensors`) was dropped from the action catalogue — actionable insights are
> performance-only. The `data_quality` rule still fires into `/api/insights` for the audit trail.

"Own baseline" is always the **longest** configured window; "live window" is `INSIGHT_LIVE_WINDOW`
(§4.6). Before 2026-08-04 the *now* side was the shortest `PAST_WINDOWS` entry (5 m).

**Evidence base.**
- `load_spike` is the best-evidenced of these: current running research finds injuries are driven
  by doing too much in a **single session relative to recent history** — a ≥30% single-run spike
  carried a 64% higher injury rate — while week-to-week change and the acute:chronic ratio showed
  little or no predictive value.
- `accumulated_load` follows Kalkhoven's first-principles model: repetitive loading accumulates
  damage, damage lowers the critical threshold, and injury occurs when load exceeds that
  *declining* threshold. High-and-still-rising is the mechanistically meaningful state.
- `residual_load` reads `ci_low` at the furthest horizon. ⚠️ **Its meaning changed when
  `dose-scenario-1` was retired (§2).** Under that model `ci_low` was a genuine counterfactual —
  "where risk settles if the athlete stops right now", closed-form decay of load already taken —
  and that is what made a recovery instruction safe to derive from it under SPEC §2. Under
  `trend-ols-1` it is the **lower bound of a statistical prediction interval**: estimator
  uncertainty around a trend, not a stop-now branch. The rule still fires on a defensible
  signal (projected risk stays above the review threshold even at the optimistic end of the
  interval), but **copy must not say "even if they stop now"** — that asserts a behavioural
  counterfactual the current model does not compute.
- `impact_deviation` is held at `info` deliberately: IMU jerk has never been validated against
  GRF loading rate (SPEC §5.2) and peak tibial acceleration does not track internal tibial load
  (Matijevich 2019, r = −0.29 ± 0.37). They are surrogates for *external* impact loading.
- `movement_quality` is `info` and flagged `unvalidated` for two independent reasons: `m4`/`m5`
  have no real-data validation at all (SPEC §11.1), and the largest prospective test of asymmetry
  (Malisoux 2024, n = 836) found **greater** asymmetry associated with **lower** injury risk.
  Magnitude only, never a direction (SPEC §5.5).

### 4.4 Gating — when a rule must stay quiet

Every within-athlete deviation rule (`load_spike`, `accumulated_load`, `impact_deviation`,
`movement_quality`) is gated on `ctx.trustworthy`; the absolute-threshold and forecast rules
(`composite_high`, `residual_load`, `rising_risk`) are not:

- **Coverage** ≥ 10% of the window, so a sliver of data cannot masquerade as a session.
- **Quality match** between the two windows being compared (within 0.75×). Measured 2026-08-03:
  5% packet loss moves `m1` **−49%** and `m2` **+34%** — in opposite directions — so comparing
  windows recorded at different link quality compares measurement artefacts, not the athlete.

Rules about the *data* (`data_quality`) deliberately bypass this gate; that is their subject.

### 4.5 Specificity over sensitivity

Thresholds sit at ~2 sd of the athlete's own spread with AND-conditions, not at any excursion. At
realistic injury base rates roughly **90% of individual alerts are false** even for a genuinely
good composite (SPEC §2, AUC 0.55–0.57 in the largest prospective test), so more rules firing
more often makes the feed worse, not better. The catalogue is deliberately small.

**Not implemented, by intent:** any acute:chronic workload ratio. The conventional form is
mathematically coupled (Lolli 2019) and has no evidence supporting its use in load management
(Impellizzeri 2020) — SPEC §6.3. `test_firing_depends_on_dispersion_not_on_a_bare_ratio` pins the
behavioural difference: the identical 2× short/long ratio fires for a metronomic athlete and
stays silent for a variable one, which a ratio cannot express.

### 4.6 Insight cadence — how fast an action appears, and why it does not flicker

Four settings decide this and are tuned **as a set**. Changing one alone reintroduces either
latency or flicker.

| Setting | Default | What it controls |
|---|---|---|
| `INSIGHT_LIVE_WINDOW` | `30s` | the window rules read as **now** |
| `INSIGHT_INTERVAL_S` | `15` | how often rules are evaluated |
| `INSIGHT_COOLDOWN_S` | `120` | how often one rule may re-fire |
| `INSIGHT_HOLD_S` | `150` | how long an action stays on `/api/insights/current` |

**The latency problem.** Detection used to run off the shortest `PAST_WINDOWS` entry, 5 minutes.
A 5-minute *mean* cannot move quickly by construction: a step change needs ~1.5–2 min of new data
before the average crosses a threshold, and the job then ran only once a minute. Measured on the
VPS 2026-08-03, the first insight of a simulated session appeared **5–9 minutes** in.

**The fix.** `INSIGHT_LIVE_WINDOW` is a separate short window, always read from the raw 60 Hz
`metrics` table — never `metrics_1m`, which is materialized-only and so is missing its newest
1–2 minutes, i.e. most of a sub-minute window. It supplies **only** the `now` side of every
comparison. Baseline, spread and trend still come from `PAST_WINDOWS`, because 30 s cannot define
what is normal for an athlete or which way they are heading. Time to first action is now
**~30–45 s**. `test_live_window_fires_on_a_step_the_5m_mean_would_still_be_averaging` measures
exactly this: two jobs differing only in `INSIGHT_LIVE_WINDOW`, 40 s into a step change, where
the 30 s window fires and the 5 m one is still averaging.

30 s is chosen because it holds ~30 independent `m1`/`m2` samples (both are 1-second peak holds)
and ~1800 composite samples — enough for a stable mean, short enough to respond within one window.

**Why it does not flicker.** Stability is provided by `INSIGHT_HOLD_S`, not by slowing detection
down. An action stays on the panel until the rule behind it has been silent that long, so a
condition hovering around its threshold does not blink. `INSIGHT_HOLD_S` **must exceed**
`INSIGHT_COOLDOWN_S`: at equality a still-true condition drops off for one tick before it
re-fires. The 30 s excess is that overlap, and `test_defaults_load_without_env_file` pins the
inequality.

Net behaviour: appears in ~30–45 s, re-affirms every ≤2 min, clears within ≤2.5 min of the
condition ending.

**Honest limit.** A rule that compares the athlete to *themselves* cannot fire until there is
enough history to define "themselves" — in the first minute of a fresh device, live ≈ baseline by
construction and `z` ≈ 0. Only the absolute-threshold rules (`composite_high`) and data-health
rules can speak that early. This is a property of within-athlete comparison, not a defect, and it
is why the panel can be legitimately empty at the start of a session.

### 4.7 From rules to actions — the `/api/insights/current` view

Rules are **diagnostic** (one per mechanism); advice is not. `load_spike` and `composite_high` are
different findings that lead to the same instruction, and rendering "Ease off" twice with two
rationales is noise, while rendering it once with two reasons underneath is evidence.

`ACTIONS` (in `backend/api/jobs/insights.py`) is the catalogue; every rule carries an `action_id`
into it, and several share one on purpose:

| `action_id` | Headline | Rules |
|---|---|---|
| `ease_off` | *Drop the next block down one level* | `load_spike`, `composite_high` |
| `cap_session` | *No more hard sets — easy work only* | `accumulated_load` |
| `plan_recovery` | *Leave a longer gap before the next hard block* | `residual_load`, `rising_risk` |
| `lower_landings` | *Lower the landing height or cut the reps* | `impact_deviation` when **m1** moved |
| `soften_landings` | *Soften the landings — check the surface* | `impact_deviation` when **m2** moved |
| `flag_review` | *Coach technique on the next block* | `movement_quality` |

(`check_sensors` — *Re-seat the straps and check the battery* — was removed from the catalogue
2026-08-05, demo posture: hardware maintenance is not a performance action. `data_quality` is
event-log only.)

**Headlines name a lever (revised 2026-08-04).** They were deliberately 2–3 words on the
reasoning that a headline long enough to wrap stops reading as an instruction. Reversed by
product-owner decision: at that length the headline was not actionable — a trainer reading
*"Check mechanics"* learns nothing they can do. Headlines now name the lever the trainer
controls (intensity, sets, landing height, surface, the gap, the straps) and stay one short
clause. What they may **not** say is bounded by evidence, not taste: never a body part,
tissue or outcome (`m1`/`m2` are surrogates for *external* impact loading — Matijevich 2019,
r = −0.29 ± 0.37); never a foot-strike prescription (the sensors are shank+thigh and the
model is movement-agnostic by mandate, SPEC §1.2, so foot contact is simply not observed);
never a stretch or treatment (PRD §5, "no medical claims"). Timings must be model
arithmetic — the only defensible one available is `m3`'s decay, which is **two pools**
(`DOSE_HALFLIFE_S` 15 min for easy work, `DOSE_HALFLIFE_SLOW_S` 90 min for hard).

**`check_mechanics` was split.** It carried `impact_deviation` (m1/m2) and
`movement_quality` (m4/m5) under one imperative despite very different evidence licence:
SPEC §11.1 forbids presenting m4/m5 to a trainer as a finding at all. Grouping them let
unvalidated evidence sit behind concrete advice about landings. `flag_review` now keeps that
advice soft and separate, and `test_unvalidated_advice_never_shares_a_headline_with_validated_advice`
pins the property.

**`Rule.action_id` may be a callable.** `impact_deviation` already knew whether `m1` or `m2`
deviated and discarded it to emit one generic headline; peak shock (height/volume) and
loading rate (technique/surface) call for different levers, so it now routes to
`lower_landings` or `soften_landings`. Resolved once, at insert time, via
`Rule.resolve_action_id()`.

**`Action.tip`** is a static coaching cue per action — the same text every time it fires,
**not** derived from the athlete's data. The UI renders it under a "Coaching cue" label
(demo posture 2026-08-05; it was "General cue — not measured" while hedged framing applied).

`group_actions()` collapses the rows inside the hold window: group on `action_id`, keep **only the
newest row per rule** (the hold/cooldown overlap guarantees duplicates), rank by severity then by
catalogue order, and cut to `INSIGHT_MAX_ACTIONS` (3). An action takes the strongest severity among
its reasons.

An action is flagged `unvalidated` only when **every** reason behind it comes from `m4`/`m5`.
Flagging one that a validated metric also supports would understate the finding; flagging none when
all of them are unvalidated would overstate it (SPEC §11.1).

`/api/insights/current` is a **state** view — the advice standing right now. `/api/insights`
remains the append-only event log, and both are served from the same table.

## 4bis. Earlier insight fixes

- **Cooldown ranks severity.** Keyed on `(device_id, rule_id)` alone, a `composite_high`
  *warning* swallowed a genuine *alert* arriving seconds later for the full cooldown
  (600 s at the time; 120 s now) — the
  escalation a trainer most needs to see was the one case guaranteed to be dropped.
  Suppression is now one-directional: strictly-more-severe passes, equal and lower do not.
- **`data_quality` reports a change, not a level.** The absolute 0.8 threshold fired
  forever on the current hardware (measured link quality 0.35) and so carried no
  information; and `quality` is a ratio against the *configured* `EXPECTED_INPUT_HZ`, so an
  absolute test partly measures that constant rather than the link. The rule now fires when
  the shortest window falls below 0.8 × the device's own longest-window baseline — which is
  actionable (a strap moved, a sensor is failing) and self-clears.

**Not changed: `composite_high`'s 85/92 thresholds.** They are miscalibrated — they were
chosen against *instantaneous* composite readings (a measured hard interval session reads
~77) but the rule tests a window **mean**, and for a spiky bounded series mean ≪ p95 ≪ max.
Correcting this needs re-calibration data that does not exist (712 rows locally). Changing
the statistic without re-calibrating would silently change what an alert means to a
trainer, which is the SPEC §2 failure mode. Deferred deliberately.

### 4.8 The advice timeline — `/api/insights/timeline` (2026-08-06)

The Insights tab used to read only `/current`, so a page reload wiped anything older than
the 150 s hold window — advice appeared to "start from scratch". Insights were **always
persisted** (the `/api/insights` event log, kept forever); what was missing was a read-side
view of the history. `/timeline` is that view:

- **Buckets come from `PAST_WINDOWS`** — the same config the History tab uses, resolved at
  request time, so changing the windows re-shapes the timeline with no code change. Bucket 0
  is `live` (exactly `/current`'s `INSIGHT_HOLD_S` definition); each later bucket spans
  (previous edge, window]. A window shorter than the hold is skipped (it would be an empty
  range). Nothing older than the longest window is returned.
- **Each bucket is collapsed by the same `group_actions()`** as `/current` — group on
  `action_id`, newest row per rule, ≤ `INSIGHT_MAX_ACTIONS` per bucket, `data_quality`
  event-log-only — then re-ordered **newest-first within the bucket**: the timeline is a
  chronology, not a severity ranking (severity still colours the card chip/edge).
- **The same `action_id` may recur across buckets.** A condition that kept firing across an
  hour IS the story; deduping across buckets would erase it. Within one bucket it still
  collapses to a single card.
- `/current` is unchanged and remains the pure live view (the overview card's one-line
  insight and any external consumer keep working). Pinned by
  `test_timeline_buckets_follow_past_windows`.

### 4.9 Decision capture — Adopt / Override (2026-08-07)

Every advice card carries **Adopt** and **Override** buttons; each press is stored in the
append-only `insight_decisions` table (migration 004, BACKEND_SCHEMA §1) via
`POST /api/insights/decisions`, and the newest decision per card rides back on
`/timeline` as `action.decision`. Design points, all user decisions 2026-08-07:

- **A "card" is one firing**: `(device_id, action_id, action_updated_at)`. The decision
  follows the card as it ages through the buckets; a re-fired action is a fresh card
  with fresh buttons — each firing is decided separately.
- **Changeable, newest wins, nothing overwritten**: pressing "change" inserts a newer
  row. The full press history is the point — which advice was adopted, which was
  overridden and what the trainer did instead is exactly the adherence dataset a future
  sports-scientist pass will want.
- `note` is stored only for overrides; blank → NULL, which reads as "overridden,
  no comment". `decided_by` records the session username.
- Pinned by `test_decisions_store_and_ride_the_timeline`.

## 5. Deliberately not done

| Not done | Cost |
|---|---|
| `materialized_only = false` on `metrics_1m` | The newest 1–2 min stay invisible to the 30 m and 2 h windows. Needs a migration. |
| `count(m1..m5)` / `max(m1..m5)` columns | m1/m2/m4/m5 means stay unweighted and null-blind, and no peak/percentile statistic is available for the primitives — so m1/m2 are still reported as *means*, the wrong statistic for a peak-hold metric. Needs a cagg rebuild, which is free now (712 rows) and impossible later for old buckets (the refresh policy's 2 h `start_offset` never recomputes them). |
| `insufficient_history` trend state | "No data" still renders as a steady arrow — a fabricated claim about an athlete, closer to a SPEC §2 concern than a cosmetic one. |
| Re-calibrating `composite_high` | Gated on data that does not exist. |

## 6. Acute:chronic workload ratio — do not implement

SPEC §6.3 already rules ACWR out and the current literature is firmly behind that.

- [Impellizzeri et al. 2020, *IJSPP* 15(6):907](https://journals.humankinetics.com/view/journals/ijspp/15/6/article-p907.xml) —
  no evidence supports ACWR in training-load-management systems; the statistical
  properties of the ratio make it inaccurate and complicate interpretation. Manipulating
  ACWR to change injury rates is "conjecture and overinterpretation".
- [Lolli et al. 2019](https://pubmed.ncbi.nlm.nih.gov/29101104/) — the conventional
  (coupled) form is **mathematically coupled**: the acute period is part of the chronic
  denominator, producing spurious correlation even when the two are genuinely independent.
- [Foster's monotony/strain](https://pmc.ncbi.nlm.nih.gov/articles/PMC5673663/) is
  separately criticised as dominated by session duration and blind to training *content*.

The literature's 7 d / 28 d timescales are also for **daily session-RPE**, whereas this
signal is a 60 Hz mechanical load whose own accumulator decays in two pools, with
15 min (easy) / 90 min (hard) half-lives.

**If a multi-day term is ever added** it should follow SPEC §7.3: two *uncoupled* EWMA load
series (τ ≈ 3.5 d and 14 d) reported **side by side and never as a ratio**, computed from
`metrics_1m`. It is deliberately not built now — there is no multi-week data to validate it
against, so it would ship unvalidated.

## 7. Durations — forecasts applied, windows still provisional

TRD §7 now sets `FUTURE_HORIZONS=10m,30m,1h` (applied; `1d,3d,1w` struck). The
`PAST_WINDOWS` recommendation `1h,1d,7d` is not yet applied. Both are `.env` values,
so changing them is configuration.

- **Forecasts: keep `10m,30m,1h`; strike `1d,3d,1w`.** Dose decays in two pools — 15 min
  (easy) / 90 min (hard) half-lives — so by one day both pools are ~0 and the "forecast"
  is just "returns to baseline". The acute term is unforecastable beyond persistence. The
  current *test* durations are the correct *production* durations.
- **Windows: `1h, 1d, 7d`.** 1 h is the session scale (1 h ≈ 4 fast half-lives (0.7 slow)),
  1 d the day, 7 d a training week. 3 d is neither. Provisional — no multi-day data exists.

## 8. The composite = 100 anomaly is still unexplained

Reported during simulator replay: the composite reached 100 in the 30 m/2 h window
min/max where analysis predicted 10–30. Two candidate explanations were tested on
2026-08-03 by replaying `example/squats.bin` through `compute()` six times (14,202 ticks,
~4 min simulated) — **both are refuted**:

1. **Length bias in `max`.** Rejected on algebra: `acute` reaches 100 only when
   `demand ≥ capacity`, which a longer window can make more *likely to contain* but cannot
   manufacture. Note also that the min/max aggregation itself is sound —
   `min(composite_min)`/`max(composite_max)` is a valid decomposition, so the query is not
   the culprit.
2. **`m4`/`m5` saturating and collapsing `capacity` to 30.** Looping the file *does* push
   past their 60 s / 30 s warm-ups (m4 emitted on 6,143 ticks, m5 on 10,417 — unlike the
   one-shot replay, where neither ever emits). But the values stay small: **m4 max 12.09,
   m5 max 38.88, degradation max 38.88 → minimum capacity 72.78, demand max 38.61,
   composite max 49.56, zero ticks at 100.**

So the loop-wrap discontinuity **on its own does not cause it** — biomech's response to
looped `squats.bin` is well-behaved. That narrows the search to the parts of the live path
this offline replay bypasses: the UDP jitter buffer, clock alignment and u32 timestamp
unwrap, and the ticker's hold-last/`active` framing (TRD §4 steps 2–5, SPEC §7.2.1). That
is the ingest path, not the analytics layer. **Window min/max should not be trusted until
this is closed.**
