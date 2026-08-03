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
`ctx.horizons`, every projected horizon with its full scenario band. Rules are not permitted to
depend on one number: `load_spike` reports what the projection says about where it is heading,
and `accumulated_load` combines the dose's past trend with the future residual.

### 4.3 The rules

| Rule | Severity | Fires on | Time axis | Action |
|---|---|---|---|---|
| `load_spike` | warning → alert at 3 sd | composite ≥ 2 sd above own baseline | past + present + future | *Ease off for the rest of this session* |
| `accumulated_load` | warning → alert | `m3` ≥ 2 sd above own baseline **and** trending up | past + present + future | *Cap this session and protect recovery* |
| `residual_load` | warning | forecast `ci_low` at the furthest horizon ≥ warn threshold | future | *Schedule recovery before the next session* |
| `impact_deviation` | info | `m1` or `m2` ≥ 2 sd above own baseline | past + present | *Review landing mechanics* |
| `movement_quality` | info, `unvalidated` | `m4` or `m5` ≥ 2 sd above own baseline | past + present | *Flag for review at the next check-in* |
| `composite_high` | warning → alert | shortest-window mean ≥ configured threshold | present | *Reduce training intensity* |
| `rising_risk` | warning | mid-window trend up **and** a projection crossing alert | past + future | *Schedule rest before the next block* |
| `data_quality` | info | shortest-window quality < 0.8 × own baseline | data health | *Check sensor fit* |

**Evidence base.**
- `load_spike` is the best-evidenced of these: current running research finds injuries are driven
  by doing too much in a **single session relative to recent history** — a ≥30% single-run spike
  carried a 64% higher injury rate — while week-to-week change and the acute:chronic ratio showed
  little or no predictive value.
- `accumulated_load` follows Kalkhoven's first-principles model: repetitive loading accumulates
  damage, damage lowers the critical threshold, and injury occurs when load exceeds that
  *declining* threshold. High-and-still-rising is the mechanistically meaningful state.
- `residual_load` is the one genuinely new capability the `dose-scenario-1` forecast unlocked. It
  reads `ci_low` — where risk settles **if the athlete stops right now** — which is closed-form
  decay of load already taken, not a prediction about behaviour. That distinction is what makes
  it safe to act on under SPEC §2.
- `impact_deviation` is held at `info` deliberately: IMU jerk has never been validated against
  GRF loading rate (SPEC §5.2) and peak tibial acceleration does not track internal tibial load
  (Matijevich 2019, r = −0.29 ± 0.37). They are surrogates for *external* impact loading.
- `movement_quality` is `info` and flagged `unvalidated` for two independent reasons: `m4`/`m5`
  have no real-data validation at all (SPEC §11.1), and the largest prospective test of asymmetry
  (Malisoux 2024, n = 836) found **greater** asymmetry associated with **lower** injury risk.
  Magnitude only, never a direction (SPEC §5.5).

### 4.4 Gating — when a rule must stay quiet

Every rule that makes a claim about the *person* is gated on `ctx.trustworthy`:

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

## 4bis. Earlier insight fixes

- **Cooldown ranks severity.** Keyed on `(device_id, rule_id)` alone, a `composite_high`
  *warning* swallowed a genuine *alert* arriving seconds later for the full 600 s — the
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

## 5. Deliberately not done

| Not done | Cost |
|---|---|
| `coverage` field (`sum(n)/(OUTPUT_HZ × window_s)`) | A window average is uninterpretable — a full hour of data is indistinguishable from four minutes of it. One arithmetic expression, but a new API field. **Cheapest item here.** |
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
signal is a 60 Hz mechanical load whose own accumulator has a 45-minute half-life.

**If a multi-day term is ever added** it should follow SPEC §7.3: two *uncoupled* EWMA load
series (τ ≈ 3.5 d and 14 d) reported **side by side and never as a ratio**, computed from
`metrics_1m`. It is deliberately not built now — there is no multi-week data to validate it
against, so it would ship unvalidated.

## 7. Durations — recommendation, not yet applied

TRD §7 sets production `PAST_WINDOWS=1h,1d,3d` and `FUTURE_HORIZONS=1d,3d,1w`. Both are
`.env` values, so changing them is configuration.

- **Forecasts: keep `10m,30m,1h`; strike `1d,3d,1w`.** Dose has a 45-min half-life; by one
  day it has decayed to ~10⁻⁴ of its value and the "forecast" is just "returns to
  baseline". The acute term is unforecastable beyond persistence. The current *test*
  durations are the correct *production* durations.
- **Windows: `1h, 1d, 7d`.** 1 h is the session scale (~1.3 dose half-lives), 1 d the day,
  7 d a training week. 3 d is neither. Provisional — no multi-day data exists.

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
