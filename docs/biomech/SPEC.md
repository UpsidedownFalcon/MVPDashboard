# Biomech SPEC — 5 primitives + composite injury risk

| | |
|---|---|
| Status | **Draft for user approval** (S1-T14). Implemented by S1-T15 in `backend/ingest/biomech.py`. |
| Supersedes | `biometrics_model.py` + `biometrics_model_rationale.md` in this folder — **reference only, stale**. §12 lists what changed and why. |
| Evidence | Every non-obvious choice is either **[V]** verified against `example/squats.bin` this session, **[L]** literature-sourced (§13 refs), or **[E]** an engineering decision with no literature backing — tagged inline. |
| Related | [../TRD.md](../TRD.md) §4 · [../BACKEND_SCHEMA.md](../BACKEND_SCHEMA.md) §§2,4,5 · [../tasks/STAGE1.md](../tasks/STAGE1.md) S1-T14/T15 |

---

## 1. Design constraints (user-mandated, non-negotiable)

1. **No orientation estimation.** No absolute angles, no complementary filter, no Kalman filter,
   no "bone-aligned axis" assumption. Every quantity is a **rotation-invariant magnitude**
   (`|a|`, `|ω|`) or a derivative. §3 shows this is not a compromise — for this hardware it is
   the *more accurate* choice, and §3.2/§3.3 prove it quantitatively.
2. **Movement-agnostic.** Same primitives, same composite, for running, walking, squatting,
   lifting, jumping. No activity classifier, no per-activity mode.
3. **0–100 arbitrary units** for all six outputs, interpretable by a non-expert.
4. **Causal only.** No lookahead, no `filtfilt`, no future samples.
5. **Interface unchanged:** `compute(frames, state) -> Metrics(m1..m5, composite)` at `OUTPUT_HZ`
   per device. Extensions in §10.

### 1.1 What the five primitives mean

| ID | Name | Construct | Question |
|---|---|---|---|
| `m1` | Impact | Magnitude | How **hard** is the loading? |
| `m2` | Loading Rate | Rate | How **fast** does load arrive? |
| `m3` | Accumulated Load | Dose | How **much** has been accumulated? |
| `m4` | Movement Control | Quality | How **differently** is it being absorbed vs. fresh? |
| `m5` | L/R Balance | Symmetry | How **evenly** is it shared? |
| `composite` | Injury Risk | Load vs capacity | Is demand outrunning current capacity? |

---

## 2. ⚠️ Claims limits — read before writing any UI copy

The literature is unambiguous on three points, and the product must not overstate them.

1. **This is not bone load.** Peak tibial acceleration correlates with *internal* tibial
   compressive force at **r = −0.29 ± 0.37** (Matijevich 2019) and **r = 0.04 ± 0.14**
   (Zandbergen 2023) — i.e. not at all. **[L]** `m1`/`m2` are surrogates for **external impact
   loading rate**, nothing more. Never label them "bone load" or "tibial stress".
2. **No composite injury score has ever passed prospective validation.** The largest test of
   commercial composites (Bird 2023, n = 689 Marine officer candidates, 10-week prospective
   follow-up) found **AUC 0.55–0.57**, and ML models classified injury "by chance". A review of
   204 published sports-injury prediction models (Bullock 2022) concluded: *"No models could be
   recommended for use in practice."* **[L]**
3. **Base rates make individual alerts mostly false.** At a 5% injury incidence, even a genuinely
   good composite (AUC 0.75, sens 80% / spec 60%) yields **PPV ≈ 9.5% — about 90% of alerts are
   false alarms.** At sens 90% / spec 90% it is still only 32%. **[L]**

**Consequence for this spec:** `composite` is a **monitoring and triage aid** — "this athlete's
loading and movement quality are drifting; worth a look" — not a prediction. Defensible UI
language: *"elevated load"*, *"deviating from baseline"*, *"flag for review"*. Indefensible:
*"predicts injury"*, *"X% chance of injury"*. **Recommendation for the trainer UI: display the
composite as a trend, not a verdict, and never fire an individual alert without showing the
component panel that drove it.** (§11 display rules.)

---

## 3. Hardware, scaling and the orientation-free core

### 3.1 Scaling — VERIFIED

Sensor **ICM-45686**, ±16 g / ±2000 °/s, confirmed by the user and **independently verified
against `squats.bin`**: median resultant accel magnitude over the whole log is **2046–2050
counts**, i.e. 1 g at 2048 LSB/g. **[V]**

| Quantity | Sensitivity | Scale factor | Unit |
|---|---|---|---|
| Accelerometer | 2048 LSB/g | `9.81 / 2048` = 4.7900e-3 | m/s² |
| Gyroscope | 16.384 LSB/(°/s) | `1 / 16.384` = 6.1035e-2 | °/s |

Compile-time constants in `backend/common/scaling.py` (user decision).

### 3.2 Why magnitude, not axes — the stale model's axis is empirically wrong

`biometrics_model.py` declares `shank_axial_axis = "z"` and extracts that single axis as the
"bone-aligned" impact primitive. In `squats.bin`, gravity actually sits on **`ay`** on all four
sensors: **[V]**

| Sensor | mean `ax` | mean `ay` | mean `az` |
|---|---|---|---|
| left shin | 309 | **1837** | −524 |
| left thigh | 520 | **1708** | −59 |
| right thigh | −68 | **1799** | −65 |
| right shin | 47 | **1909** | −141 |

An axial primitive reading `az` would report a near-zero, meaningless channel — and the correct
axis changes with how the strap was fastened. Resultant magnitude has no such failure mode.

**What resultant costs, honestly.** Axial PTA correlates with vertical loading rate at pooled
**r = 0.72**; resultant at **r ≈ 0.47–0.67** (Tenforde 2020, n=169) and **0.57–0.61**
(Van den Berghe 2019). **You lose roughly 35–40% of explained variance.** **[L]**

**What resultant buys, and why it wins here.** Between-session reliability is *better* for
resultant: **ICC 0.53–0.81 vs 0.50–0.59 for axial** (Van den Berghe 2019) — because it does not
depend on re-aligning the sensor. For a field system with daily re-donning by a trainer, that
offsets the correlation loss. Independently, Sarantos 2025 recommends resultant *specifically
because* "sensor axis alignment differences between gait conditions may confound vertical
acceleration results". **[L]** Vicon's shipping IMU Step product also uses peak **resultant**
acceleration. The orientation-free constraint is well-supported, not a concession.

### 3.3 Gravity removal — `|a|` high-pass, NOT per-axis (decisive)

Three methods tested against a synthetic **pure rotation of a stationary sensor** — no linear
acceleration at all, so any non-zero output is pure artifact: **[V]**

| Rotation rate | **A: `\|a\| − LPF(\|a\|)`** | B: `\|a − LPF(a)\|` (VeDBA/ODBA) | C: `\|da/dt\|` |
|---|---|---|---|
| 90 °/s | **0.0000 m/s²** | 4.73 m/s² | 15.4 m/s³ |
| 180 °/s | **0.0000** | 7.26 | 30.8 |
| 720 °/s | **0.0000** | 9.54 | 123.3 |

**Method B — the standard dynamic-body-acceleration approach (ODBA/VeDBA), used by the stale
model and most of the accelerometry literature — is unusable on a limb.** At 180 °/s (ordinary
squat thigh rotation; measured p95 is 173–181 °/s) it fabricates **7.26 m/s² of impact that does
not exist**, larger than the real 4.7 m/s² squat signal. The per-axis low-pass is a ~0.3–1 Hz
high-pass, while limb gravity-sweep is 2–4 Hz — the artifact passes straight through. This is
corroborated independently: ODBA/VeDBA were designed for slowly-reorienting **trunk** mounts on
animals, and the `ℓ1` ODBA norm is not even rotation-invariant. **[V] + [L]**

**Method A is exactly gravity-immune at any rotation rate**, because `|g|` is constant under
rotation. This is the single most important decision in this spec.

**Method A's honest weakness.** Since `|a| = |g + d|`, dynamic acceleration *perpendicular* to
gravity is geometrically compressed: **[V]**

| True dynamic accel | Parallel to g | Perpendicular to g |
|---|---|---|
| 1 m/s² | 100% | 5% |
| 10 m/s² | 100% | 42% |
| 50 m/s² | 100% | 82% |
| 100 m/s² | 100% | 91% |

Compression only bites at low amplitude; impacts read ≥70% worst-case. Slow horizontal gym
movement is under-read — which is why `m3` carries a rotational term (§5.3) and why `|ω|` is the
more trustworthy channel for lifting. **[L]** Two further consequences to encode: `|a|` also
**folds over at free-fall** (a downward 1 g reads 0), and it **re-crosses 9.81 mid-movement**
whenever dynamic accel is perpendicular to gravity — so `|a|` must never drive event detection.

### 3.4 `m2` uses an EXACT gravity-free, rotation-invariant jerk

Differentiating the specific-force vector gives, with `Ω = [ω]ₓ`:

```
  ḟ_b = Rᵀ ȧ_w − ω_b × f_b        (gravity is constant in the WORLD frame, so it differentiates away)
⟹ | ḟ_b + ω_b × f_b |  =  | ȧ_w |     ← exactly gravity-free AND exactly rotation-invariant
```

Both `f_b` (accel) and `ω_b` (gyro) are **directly measured**. No orientation estimate, no
filter, no cutoff, no assumption. **Verified numerically** against synthetic ground truth: **[V]**

| Rotation rate | Naive `\|ḟ\|` error | **Corrected `\|ḟ + ω×f\|` error** |
|---|---|---|
| 180 °/s | 16.19 m/s³ | **0.0005 m/s³** |
| 720 °/s | 86.99 | **0.0103** |
| 1500 °/s | 192.65 | **0.0564** |

On the squats replay the correction changes `m2` by only ~0.5% (squat rotation is modest), but
during running swing at 1000+ °/s the naive residual reaches ~171 m/s³ — material against the
981 m/s³ osteogenic jerk threshold. The correction is one cross-product per sample; take it.

**Requirement this imposes:** accelerometer and gyro must be **sampled synchronously** — the
cross term multiplies them. They share one packet in our wire format (TRD §3), so this holds.

### 3.5 Processing chain (per limb, causal, rate-adaptive)

```
raw counts (n×6)
  → scale to SI, APPLYING CALIBRATION (§3.8):
        a_vec  = counts_a · (k · 9.81/2048)             [m/s²]   k = per-sensor gain, default 1
        ω_dps  = counts_g · (1/16.384) − gyro_bias      [°/s]    bias default (0,0,0)
  → low-pass, 2× cascaded one-pole, fc = 75 Hz   (rate-adaptive α, §3.6)
  → amag  = ‖a_vec‖                  rotation-invariant, gravity included
  → base  = one-pole LPF(amag), τ = 0.35 s       tracks the constant |g|
  → adyn  = |amag − base|            DYNAMIC ACCEL MAGNITUDE  [m/s²]  → m1, m3, m4, m5
  → ω_rad = ω_dps · π/180            ⚠️ RADIANS — required by the cross product
  → jerk  = ‖Δa_vec/Δt + ω_rad×a_vec‖  EXACT LINEAR JERK      [m/s³]  → m2
  → wmag  = ‖ω_dps‖                  ROTATIONAL INTENSITY     [°/s]   → m3, saturation
```

🚩 **Unit trap:** the `ω×a` term **requires ω in radians/second**. Feeding °/s makes `m2` wrong by
a factor of **57.3**, and it will look plausible rather than obviously broken. `wmag` stays in
°/s because its reference range (§4) is expressed in °/s. S1-T15 must assert this (test 2b).

`Δt` uses actual timestamps; the first sample of each tick differences against the last sample
of the previous tick (carried in `state`), so there is no per-tick discontinuity.

**Per-tick reduction (this IS the metric definition, not an optimisation — §5.1/§5.2):** each
tick reduces its ~10 samples to `p90(adyn)`, `p90(jerk)`, `mean(adyn)`, `mean(wmag)`. The
**p90 within the tick** rejects single-sample spikes; a real impact spans 10–30 samples at
600 Hz and passes through untouched.

### 3.6 Filter: 75 Hz, not 50 Hz — and why the cutoff matters more for jerk

The stale model used 50 Hz. The literature spread for tibial acceleration is 40–100 Hz, with
**60 Hz modal**; 99% of tibial-acceleration signal power lies below 60 Hz. 50 Hz is defensible
*for peak acceleration*. **It is not defensible for jerk:** **[L]**

| Cutoff | Peak **acceleration** retained | Peak **jerk** retained |
|---|---|---|
| 20 Hz | 64% | 5–45% |
| **50 Hz** | **97%** | **36–75%** |
| 75 Hz | ~100% | 58–82% |
| 100 Hz | 100% | 69–89% |

Differentiation weights the band edge by `2πf`, so jerk is far more cutoff-sensitive than peak
acceleration. **75 Hz** is chosen: it retains essentially all peak acceleration, recovers most
peak jerk, and sits at `fs/8` (600 Hz input) — comfortably below Nyquist and below documented
accelerometer/mount resonances (250–1000 Hz).

**Filter type — one-pole cascade, not Butterworth.** A fixed-coefficient IIR assumes a fixed
sample rate; TRD §4 requires rate-flexible input. A cascaded one-pole with
`α = 1 − exp(−Δt/τ)` recomputed from **measured** Δt is rate-correct at any input rate, trading a
gentler roll-off for that robustness. **No `filtfilt`** — non-causal, forbidden by §1.4. **[E]**

Jerk is computed from the **low-passed** vector: raw 600 Hz differentiation has noise
`σ_jerk = σ_a · fs/√2`, and unfiltered differentiation is documented at **374% error**
(Crenna 2021). Measured rest jerk floor after filtering: ~40 m/s³ mean, ~48–57 m/s³ p99. **[V]**

### 3.7 Saturation — a live risk, and worse for running than the squat data suggests

Per-axis clipping at ±32767 counts = ±16 g / ±2000 °/s; the resultant can legitimately reach
27.7 g / 3464 °/s. In `squats.bin` there is **zero clipping**, but a single axis peaks at
**1875 °/s — only 6% below full scale**. **[V]**

🚩 **Accelerometer range is a genuine hardware concern for running.** Published resultant PTA
reaches **20.13 ± 8.97 g** in max sprint and **27.22 ± 7.94 g** in single-leg hop landing; one
study running a ±16 g device on 24 runners recorded **13,031 footstrikes exceeding ±16 g on a
single axis**, and spline restoration still underestimated peaks by ~1.4 g. Published guidance
is **≥±32 g for tibial work**. **[L]**

Actions: (a) count samples within 1% of full scale as `sat_count`, exposed via `/api/health`;
(b) **suppress `m1` and `m2` for any window whose saturated fraction exceeds 2.6%** — emit `null`
and flag, rather than a silently truncated peak.

⚠️ *The 2.6% figure is borrowed, not derived.* It comes from a **gyroscope** clipping study on
**foot-mounted** running sensors (cumulative error stays <5% below that fraction) and is applied
here to **accelerometer** suppression on the shank. The transfer is plausible — both are
"how much clipping before the aggregate is untrustworthy" — but it is **not validated for this
use**. Treat it as a starting threshold to be tuned once real running data shows the actual
saturated-sample distribution (open item 3). Clipping
also destroys jerk (a flat top has zero derivative). **This belongs in the hardware backlog:
±16 g is fine for thigh sensors and all strength training, but marginal on the shank for
running and jumping.** Gyro ±2000 °/s is adequate everywhere except kicking. **[L]**

### 3.8 Calibration — AUTOMATIC, so every session runs calibrated

**Nothing in this spec needs calibration to be correct.** There is no gravity vector to estimate,
no axis to align, no orientation to initialise — that is the whole point of §3.2–§3.4, and the
system works with zero setup on defaults `k = 1`, `gyro_bias = 0`, `σ = 0.035 m/s²`.

But a still-stand does measure three things cheaply, and **one of them matters more than it
looks**: `m4` and `m5` are **inter-sensor ratios**, so any per-sensor gain mismatch lands
directly on them as a bias that no amount of downstream processing can remove. **User decision
(supersedes the §13 open item 8 deferral): calibration is automatic and always on.** There is no
trainer-visible routine, no instruction to the athlete, and no API route — the athlete stands
still at some point in almost every session, so the model simply watches for it.

**Trigger — automatic still-detection, per sensor:**

| Stage | Rule |
|---|---|
| First **3 s** of a sensor's streaming | **Discarded** — power-on transients |
| Acceptance, per tick | `mean\|ω\| < 5 °/s` **and** `\|mean\|a\| − 9.81\| < 2%` — the rejection guards below, used as the acceptance test |
| Window | **10 s of *continuous* stillness**; any failing tick resets the accumulation to zero |
| Scope | **Per sensor, independent** — sensors settle at different moments and calibrate at different moments |

The guards run on the **calibrated** magnitudes — what the pipeline actually carries. For a
sensor on defaults (`k = 1`) that is exactly the raw test; for one already running carried-over
values it tests the *residual* error, which is what lets carry-over be refined instead of
locking a corrected sensor out of ever measuring again. Measured corrections **compose** with
what is already applied (`k_total = k_applied · 9.81 / mean|a_calibrated|`).

**Running sums, never samples.** 10 s × 600 Hz × 4 sensors is ~2 MB per device to produce three
scalars, and all three are *exact* functions of `Σ|a|`, `Σω` and `Σ|a|²`:
`k = 9.81/mean|a|`, `gyro_bias = mean(ω)`, `σ = √(E|a|² − (E|a|)²)`. Only the sums are kept.

**Stand still, not "some motions."** All three products come from stillness; a motion routine
would add nothing and could not be standardised across running vs lifting users.

**Start calibrated, then refine (`biomech:cal:{device_id}`, BACKEND_SCHEMA §4).** On session
start the device's **last-known-good** calibration is loaded from a dedicated Redis key with a
TTL of days, keyed on **limb name** and never on slot index, and applied immediately — then
upgraded in place when a fresh still window lands. This is deliberately **not** the §7.4 session
snapshot: that one is discarded after `SESSION_GAP_S`, which is exactly the case where carry-over
matters (the athlete came back the next day). **Only a device with no history ever runs on
defaults.**

**What it measures** (all orientation-free — `|a|` at rest is `|g|` regardless of how the sensor
sits): **[V]** measured on the still segment of `squats.bin`:

| Output | Purpose | Measured on this unit |
|---|---|---|
| **Accel gain** `k = 9.81 / mean(\|a\|)` | Equalises sensors so `m4`/`m5` ratios are unbiased | k = 0.9989 … 1.0045, **spread 0.56%** |
| **Gyro bias** (3-vector, per sensor) | Keeps "still" genuinely still; feeds the `ω×a` jerk term and `m3`'s rotational intensity | 0.11 – 0.34 °/s resultant |
| **Accel noise σ** | **Feeds `wUSI`'s weighting term directly** (§5.5) — previously an unmeasured constant | 0.030 – 0.038 m/s² |

On this particular device the four sensors are unusually well matched, so calibration moves `m5`
by only ~1 point (19.2 → 20.1). That is a fortunate sample, not a guarantee across units — and
since the measurement is free, take it. Gyro bias is the more clearly worthwhile one: 0.1–0.34 °/s
against a still-standing `|ω|` of 3–5 °/s is 5–10% of the low-end signal.

**Validity guards — reject rather than bake in a bad correction:**
- `|mean(|a|) − 9.81| > 2%` ⇒ athlete was not still, or sensor faulty ⇒ **reject, keep defaults**.
- `mean(|ω|) > 5 °/s` ⇒ athlete moved ⇒ **reject**.
- `k` outside `[0.95, 1.05]` ⇒ **reject**. Under automatic detection the first two are the
  per-tick acceptance test, so this one bites on the *composed* result — a carried-over `k` that
  has itself drifted compounding with this session's residual.
**Distinguishing a faulty sensor from a restless athlete.** The two acceptance guards answer
different questions and are therefore applied separately. `mean|ω| > CAL_MAX_ROTATION_DPS` means
the athlete moved — it says nothing about the sensor, so the window simply restarts. But if the
gyro reports the sensor motionless while `|a|` still disagrees with gravity, motion cannot explain
it and the sensor can: after `CAL_FAULT_S` (20 s) of that state the sensor is flagged
`cal_failed`. Without the split the two causes were indistinguishable, and a genuinely mis-scaled
sensor read `uncalibrated` for the whole session — identical to an athlete who never stood still.

- On rejection: flag `cal_failed`, keep last-known-good (or defaults), **clear the window and keep
  seeking**. There is nothing for the UI to retry: detection never stops.

**What calibration must NOT attempt:** orientation, axis alignment, or limb assignment. Those are
forbidden by §1.1 and are exactly the failure mode of the stale model (§3.2).

**Persistence.** Accel gain is stable across sessions; gyro bias drifts with temperature.
Recompute per session when a still window lands, and carry the last-known-good values as
fallback. Two keys, deliberately: the §7.4 snapshot (same session, TTL `2×SESSION_GAP_S`) and
`biomech:cal:{device_id}` (across sessions, TTL days). A new session **demotes** its measured
values to carried and starts the search again — including the 3 s discard. Both are keyed on
limb name, never on slot index.

**Streaming during calibration.** The 60 Hz ticker **keeps emitting throughout**, using whatever
is currently applied. When a window lands, corrections apply from the next tick — a visible step
of up to ~0.5% in `m1`/`m3` and rather more in `m4`/`m5` if sensor gains were mismatched. This is
accepted rather than hidden: the alternative (suppressing output) looks like a dead device. The
transition is in `flags` — `uncalibrated` (defaults), `carried_over` (last session's values),
neither (measured this session) — so the UI can mark the discontinuity rather than presenting it
as a change in the athlete.

`m4`'s transmission baseline `R_base` is **not** part of this routine — it requires *movement*
representative of the actual activity, so it self-learns from the first 60 s of movement (§5.4).
A 15 s calibration wiggle would not be representative of running or of a working set.

---

## 4. Normalisation to 0–100

Measured dynamic range between lifting and running is ~50× (squat peak dynamic accel ~2 m/s²;
running impacts 50–100 m/s²), so a linear scale calibrated for running renders every gym session
as ~2/100. **[V]**

**`m1`, `m2`, `m3` use a log scale** (physically unbounded, spanning decades):
`score = 100 · clamp( ln(x/lo) / ln(hi/lo), 0, 1 )`

**`m4`, `m5` use linear clamps** — already dimensionless bounded ratios, where a log scale would
be meaningless. *(This refines the "log-scaled absolute" decision: log applies to the unbounded
magnitude primitives only. Flagged for approval.)*

| Metric | `lo` | `hi` | Basis |
|---|---|---|---|
| `m1` Impact | **`max(0.15, 5σ)`** m/s² | 150 m/s² | **`lo` is noise-adaptive** — derived from the per-sensor `σ` that calibration already measures (§3.8), so a noisier sensor cannot read m1 > 0 at rest. Measured `σ` = 0.0232 ⇒ `lo` = 0.15 **[V]**. `hi` ≈ 15 g, near accel full scale |
| `m2` Loading Rate | 120 m/s³ | 12 000 m/s³ | `lo` at measured rest jerk floor (p99 48–57) **[V]**. `hi` spans published running impact jerk **2 500–7 800 m/s³** **[L]** with headroom for landings. *Previously 30 000, which was anchored to a 26 448 m/s³ single-sample handling artifact that §5.2's p90 explicitly discards — an incoherent basis, corrected here.* |
| `m3` Accumulated Load | **0.01** | **60** (dose·min) | §5.3. Chosen so a realistic session spans the scale: 16 s squats → 14, 10 min moderate → 56, 1 h moderate → 76, 1 h hard → 88 **[V]** |
| **`ω` term (inside `m3`)** | **5 °/s** | **1500 °/s** | **Was missing entirely — `m3` was unimplementable.** `lo` above measured still-standing mean `\|ω\|` = 1.6 °/s; squat mean = 82 °/s → score 49 **[V]**; `hi` covers sprint thigh (792 °/s) and inferred shank (1200–1900 °/s) **[L]** |
| `m4` Movement Control | 0 | ±50% vs own baseline | §5.4 |
| `m5` L/R Balance | 0 | 18% wUSI | §5.5 — **literature-derived** |

Reference bounds are **constants in `biomech.py`, not `.env` keys** — model parameters, not
deployment wiring (TRD §7 permits file-local tuning constants). They are **provisional starting
points requiring calibration against trial data**, not validated clinical cut-offs.

---

## 5. The five primitives

All windows are trailing and causal. `W_PEAK = 1.0 s`; `MOVE_GATE = 0.10 m/s²` on tick-mean
`adyn` (measured still 0.025–0.032, squatting 0.65–0.94 — ~3× above noise, ~7× below movement).
**[V]**

### 5.1 `m1` — Impact

```
per tick:   s_i = percentile₉₀( adyn , this tick's ~10 samples )          [m/s²]
raw₁      = max over shank limbs of  max( s_i , last 60 ticks = 1.0 s )
```

**Two levels, and the order matters.** Artifact rejection happens *within* the tick (p90 of ~10
samples kills single-sample spikes — the squat log has isolated 13 g handling transients); the
peak-hold happens *across* the ring (**max**, not a percentile).

⚠️ **`max` across the ring is mandatory. A percentile across the ring is silently broken** — with
p90 over 60 ticks, an isolated impact must occupy >6 of 60 ticks to register at all. Measured: a
**50 m/s² single-tick impact moves p90-over-ring by 0.000** while moving max-over-ring by 49.95.
Running at 180 spm puts only ~3 strikes in a 1 s window, so a ring-percentile would systematically
under-report impacts and vary with cadence rather than load. **[V]** This two-level form tracks a
true `p99` over the raw 1 s at **1.08 ± 0.04** — tight and unbiased — where ring-p90 wandered
between 0.38× and 0.93×. **[V]**

Shank-preferred (the validated site for impact surrogates); falls back to thigh if absent, flagged
`no_shank`. Emits `null` if the window is >2.6% saturated (§3.7).

### 5.2 `m2` — Loading Rate

```
per tick:   j_i = percentile₉₀( jerk , this tick's ~10 samples )          [m/s³]
raw₂      = max over ALL limbs of  max( j_i , last 60 ticks = 1.0 s )
```
with `jerk = ‖Δa/Δt + ω_rad×a‖` (§3.4, **ω in radians/s**). Same two-level rule and the same
mandatory `max` across the ring as `m1` — see the warning in §5.1.
Loading rate — not peak magnitude — is the ground-reaction
variable most consistently associated with running injury, and it is what `m1` compresses away
for non-vertical loading.

⚠️ **Honesty flag: IMU jerk has never been validated against GRF loading rate.** No such study
exists. The nearest support is the reverse direction — GRF loading *rate* predicts peak tibial
acceleration at **r² = 0.95** (Hennig 1993) — plus a validated osteogenic **jerk threshold of
100 g/s ≈ 981 m/s³** (Jämsä 2011, hip-mounted). **[L]** Also: at 600 Hz only ~60–90% of true
peak jerk is recovered (peak jerk is ~5× more sample-rate-sensitive than peak acceleration), so
**`m2` is meaningful as a within-athlete relative trend, not an absolute physical value.** Pin
`fs`, cutoff and differentiation scheme; never compare across configurations.

### 5.3 `m3` — Accumulated Load

Mechanical intensity, then **power-law weighted** accumulation with decay:

```
intensity   = max( score(adyn_mean, m1 range), score(wmag_mean, ω range) )   0..100
dose       ← dose · 2^(−Δt / DOSE_HALFLIFE)                                  always
if intensity > gate:  dose += (intensity/100)^DOSE_EXPONENT · Δt/60          accumulate
raw₃        = dose
```

- **Mean, not sum** — a sum scales with sample count, so packet loss would silently reduce
  reported dose. Under the mean, loss costs precision, never magnitude.
- **`DOSE_EXPONENT = 3`.** Load accumulates as a **power law, not linearly**. The bone
  daily-stress-stimulus model is `ψ = (Σ nᵢ σᵢ^m)^{1/m}` with **m = 4** (Whalen 1988); fatigue
  damage is ~2.1 below a critical strain and ~5.8 above (Pattin 1996); one wearable
  implementation weights peak vGRF to the **9th** power (Kiernan 2018). **m = 3 is a deliberate
  conservative middle.** **[L]** At m = 3, doubling intensity is worth 8× the duration. This is
  why Player Load's strict linearity is **not** adopted — it cannot distinguish 1,000 soft steps
  from 100 hard ones.
- **`intensity` takes the max of an acceleration term and a rotational term.** This is what makes
  `m3` work in the gym: `|a|` under-reads slow horizontal movement (§3.3), while peak/mean `|ω|`
  is the **strongest-supported IMU fatigue marker in resistance training** (hip ω d = 1.35, knee
  d = 1.26, ankle d = 1.14 across a fatiguing squat set — Brice 2020). **[L]**
- **`DOSE_EXPONENT = 3` is kept on literature grounds, and the consequence is accepted.** With a
  cubic weighting, short bouts accumulate very little: the 16 s squat segment reaches only
  `dose = 0.033` ⇒ `m3 = 14`, so the composite's dose *floor* after a brief session is small
  (7.5, not the ~22 a linear dose gave). **This is the physically correct answer** — 16 seconds
  of moderate squatting genuinely is a negligible cumulative load — but it does mean the
  "decay-to-dose-floor" behaviour (§6.1) only becomes visually prominent over sessions of many
  minutes. A linear or squared dose would make the floor more visible at the cost of the
  magnitude-weighting the bone literature is unambiguous about. **User decision: trust the
  literature.** **[V] + [L]**
- **`DOSE_HALFLIFE = 45 min`** — session-scale fatigue residue. Multi-day load is **not** this
  metric's job: it belongs to the TimescaleDB continuous aggregates (TRD §6). If a multi-day
  term is added later, use the EWMA structure (τ ≈ 3.5 d acute, 14 d chronic) — **but not the
  acute:chronic *ratio***, see §6.3. **[L]**

### 5.4 `m4` — Movement Control

Shock **transmission ratio** between shank and thigh of the same leg:
```
R = mean(adyn, thigh, trailing 20 s) / mean(adyn, shank, trailing 20 s)
```
Measured squat `R` = **1.380 left, 1.382 right** — agreement to 0.002 across limbs, confirming
`R` is a real, repeatable quantity. **[V]** But it is *amplification* (>1) in squatting where
running gives *attenuation* (<1), so no absolute 0–100 scale is honest across both. Hence
baseline-relative, per user decision:

```
R_base = R over the first 60 s of accumulated movement in the session (locked)
m4     = 100 · clamp( |R / R_base − 1| / 0.50 , 0, 1 )
```

🚩 **The `|·|` is a correction forced by the literature, and it matters.** My first draft scored
`(R/R_base − 1)` — assuming attenuation *degrades* (R rises) with fatigue. **The evidence says
the opposite, or nothing at all:** **[L]**
- A 39-study systematic review (Marotta 2022) found shock attenuation **increases** under
  fatigue in most studies (head–tibia: 2/6 significant increase, 1/6 significant *decrease*).
- Central fatigue **increased** attenuation with a large effect (ES −1.40); peripheral fatigue
  produced no frequency-domain change at all (Encarnación-Martínez 2022).
- Injured runners showed **greater** lower-body 10–20 Hz attenuation than uninjured, not less
  (Kiernan 2026).
- The one study of the **thigh/shank pair specifically** (Sarantos 2025) found it was the single
  pair with **no significant effect** — most attenuation happens below the knee.

So the *direction* is not fixed and the thigh/shank pair is the weakest-evidenced of all. What
survives is that **any drift from the athlete's own fresh baseline is informative**, which is
also the general recommendation of the field (change detection against personal baselines, not
absolute thresholds). `m4` is therefore a **direction-agnostic deviation** metric, and its
`raw` channel retains the signed ratio so the direction can be studied from real data later.

🚩 **`R` MUST be frozen unless both sensors of that leg are live.** Absent slots are hold-last
filled (§7.2.1), so their `adyn` decays toward 0. If a shank drops out while its thigh survives,
`R = thigh/shank` explodes and `m4` pins at 100 — a hardware fault rendering as the most alarming
possible biomechanical finding: **[V]**

| thigh | shank | naive `R` | naive `m4` | with the gate |
|---|---|---|---|---|
| 0.90 | 0.650 (both live) | 1.385 | 1 | 1 |
| 0.90 | 0.100 | 9.0 | **100** | `null`, flagged `partial` |
| 0.90 | 0.001 | 900 | **100** | `null`, flagged `partial` |

Rule: update `R` only when **both** limbs of that leg are `active` **and** the shank's `adyn`
exceeds `MOVE_GATE`. Otherwise hold the previous value; if the fault persists beyond one 20 s
window, emit `null` flagged `partial`. The same guard prevents divide-by-near-zero.

Before `R_base` locks, `m4 = null`, flagged `warming_up`.

⚠️ **`m4` is NOT validated against real data — see §11.1.** The squats log holds only ~20 s of
movement, below the 60 s baseline lock, so **`m4` never emits a value on the only capture that
exists.** It ships on synthetic fixtures, validated live in S1-T15 step 4.

### 5.5 `m5` — L/R Balance

**Estimator: weighted Universal Symmetry Index (wUSI)**, not the classic Symmetry Index:
```
per tick (units m/s², same as σ):
    W_i  = 1 − 2σ² / (σ² + L_i² + R_i²)        noise-floor weight, σ from calibration (§3.8)
if both_sides_active and moving:
    accL ← accL·2^(−Δt/ASYM_HALFLIFE) + W_i·L_i·Δt
    accR ← accR·2^(−Δt/ASYM_HALFLIFE) + W_i·R_i·Δt
else:
    FREEZE — no decay, no accumulation (see the warning below)
USI  = (accL − accR) / √(accL² + accR²)
m5   = 100 · clamp( |USI| / 0.18 , 0, 1 )
```
`ASYM_HALFLIFE = 5 min`.

🚩 **The weighting must be applied PER TICK, not to the accumulator — otherwise it does nothing.**
`σ` is a per-sample noise figure in m/s²; the accumulators are in m/s²·s. Evaluating
`W` against the accumulators gives **`W = 0.99999` — an exact no-op**, so "wUSI" would have been
plain USI while the spec claimed noise protection. Applied per tick the units match and it works
as intended: **[V]**

| per-tick L, R (m/s²) | raw USI | `W` | weighted |
|---|---|---|---|
| 0.030, 0.031 (at the noise floor) | 2.3% | **0.55** | 1.3% |
| 0.050, 0.080 (barely moving) | 31.8% | 0.89 | 28.2% |
| 0.770, 0.810 (squatting) | 3.6% | 0.999 | 3.6% |
| 3.000, 3.200 (running) | 4.6% | 1.000 | 4.6% |

🚩 **Accumulators MUST freeze when either side goes inactive.** They decay continuously, so a
one-sided sensor failure lets the live side keep growing while the dead side decays — driving
`m5` to maximum within seconds. Measured, left side dead: **[V]**

| Elapsed | no gate (`accL`, `accR`) | `m5` | with the both-sides gate |
|---|---|---|---|
| 30 s | 9.33, 32.52 | **100** | 0 (frozen) |
| 60 s | 8.71, 53.53 | **100** | 0 (frozen) |
| 120 s | 7.58, 91.42 | **100** | 0 (frozen) |

Without the gate, **a dead sensor renders as "severe asymmetry" inside a minute.** If the fault
persists beyond one `ASYM_HALFLIFE`, emit `null` flagged `partial` rather than a stale frozen value.

**Why wUSI.** Benchmarked against five axioms (finite range; 0 = symmetry; identifies direction;
order-independent; scale-invariant), **only the Symmetry Angle and wUSI pass — the classic SI and
Ratio Index fail** (Alves 2020). SI is undefined when `X_R = −X_L` and inflates without bound
near zero. The `W` term explicitly suppresses noise-floor inflation at low signal amplitude,
which is exactly the failure mode during walking and light sets. **[L]**

**Why accumulated load, not instantaneous.** Established empirically — three formulations
simulated on the squat segment: **[V]**

| Formulation | Behaviour |
|---|---|
| SI of 1 s window means | Swings **8% → 172%**. Dominated by left/right *phase*, not asymmetry. Unusable. |
| SI of EMA-smoothed means (τ=30 s) | Never converges over a 19 s bout; pins `m5` at ~100. Unusable. |
| **wUSI of decaying accumulators** | Converges to **3.45% USI** (= 4.88% classic SI), stable. |

⚠️ **`m5` is NOT validated against real data — see §11.1.** The squats log holds ~20 s of
movement, below the 30 s warm-up, so **`m5` never emits a value on the only capture that exists.**
It ships on synthetic fixtures, validated live in S1-T15 step 4.

**Warm-up: `m5` is `null` until 30 s of accumulated movement.** Before that the accumulator is
dominated by the first reps — SI reads 82% at 1 s of movement, 21% at 10 s, converged by ~30 s.
Emitting during warm-up guarantees a false alarm at the start of every session. **[V]**

**Full scale = 18% wUSI — literature-derived, per your instruction.** The anchor is the one study
using *exactly this modality* (bilateral tibial accelerometry, resultant magnitude,
Delgado-García 2025): over a 30-min fatiguing run, **tibial load asymmetry rose from 9% to 25%
(r = 0.98, p < 0.001)**, while sacral asymmetry did not change at all. Converting their classic-SI
values to USI units (`USI ≈ SI/√2` near symmetry) gives **fresh ≈ 6.4%, fatigued ≈ 17.7%** → full
scale **18%**. Our squat set reads **m5 ≈ 19**, appropriate for a short unfatigued set. **[L]+[V]**

⚠️ **Frame `m5` as a fatigue/state marker, never as an injury predictor.** The largest prospective
test (Malisoux 2024, n = 836 runners, 6-month follow-up, 107 injuries) found **greater asymmetry
associated with *lower* injury risk** (flight time HR 0.80; peak braking force HR 0.96). **[L]**
Also note natural asymmetry in uninjured runners is highly parameter-specific — 2.3–3.1% for peak
GRF but **12.1–33.8% for vertical average loading rate** — so the widely-quoted "10% threshold"
has no injury-data basis and sits inside measurement error for several tests.

**Do not report direction.** Agreement on *which* limb is dominant is poor across sessions
(Cohen's κ = −0.14 to 0.60). Report magnitude only; `raw` retains the sign for later study. **[L]**

---

## 6. The composite — injury risk

### 6.1 Structure

```
demand      = 0.60·max(m1, m2) + 0.40·min(m1, m2)        soft-max, 0..100
degradation = (0.45·m4 + 0.30·m5) / Σ(available weights)  m4,m5 only — renormalised
capacity    = 100 − 0.70·degradation                     30..100
acute       = 100 · demand / (demand + capacity)
floor       = 0.50 · m3                                  accumulated-fatigue residue
composite   = floor + (100 − floor) · acute/100          0..100
```

**`m3` was removed from `degradation`.** It previously appeared in *both* `degradation` (0.25)
and `floor` (0.50) — a genuine double-count that the earlier text acknowledged but did not fix.
`m3` now enters through the `floor` term only, which is the channel the dose-floor behaviour
(§6.1) actually needs. `degradation` is what it says: movement-quality degradation.

⚠️ **Consequence during warm-up:** for the first 60 s, `m4` and `m5` are both `null`, so
`degradation` has **no available terms and is defined as 0** ⇒ `capacity = 100`. The composite is
then purely `floor + demand`-driven. This is correct — with no evidence of degradation, none is
assumed — but it means the composite's meaning is narrower in minute one, and it must be flagged
`warming_up` so the UI does not present an unqualified risk number then.

### 6.2 Why this shape

Grounded in the **load-vs-capacity** model, which the literature identifies as the only
defensible foundation: workload raises injury risk through exposure *and* fatigue while
simultaneously building capacity, so the load–injury relationship **cannot be monotonic**
(Windt & Gabbett 2017), and tissue failure is a **threshold event** (`D > D_c`), not an average
(Kalkhoven 2026). **[L]**

A plain weighted average is rejected on four documented grounds: **[L]**
1. **Fully compensatory** — excellent smoothness offsets catastrophic asymmetry, which is
   physiologically wrong under a threshold model.
2. **Cannot represent non-monotonic risk** — a linear sum's two arms cancel.
3. **Multicollinearity** — our five primitives all co-vary with intensity and fatigue; summing
   correlated components silently over-weights whatever they share.
4. **Empirically, compositing destroys information**: the FMS composite reached AUC 0.52 while a
   single component gave OR 3.47 (Kikumoto 2026).

The soft-max lets a single severe channel drive `demand` without one noisy channel pinning it
(the failure mode of a pure `max`). `capacity` floors at 30 so the composite stays bounded. The
`floor` term implements the **decay-to-dose-floor** behaviour you chose: when demand goes to
zero the composite settles onto accumulated residue, which then decays with `m3`'s 45-min
half-life rather than falsely reading zero for an athlete who just finished a hard session.

**All weights are provisional and cannot be derived without outcome data.** Weights are a value
judgement, not a measurement; with a small injury dataset they would overfit (hamstring models
have gone from within-sample AUC 0.91 to between-year AUC 0.52). They are single named constants
at the top of `biomech.py`. **[L]**

### 6.3 Explicitly NOT shipped

Per the evidence, the following are deliberately absent: **[L]**
1. **Acute:chronic workload ratio, in any form.** Monte-Carlo simulations built so ACWR has *no*
   true relationship with injury still reproduced published effect sizes (+6.8% to +10.5%);
   replacing real chronic load with **fabricated random values** gave similar associations; the
   one RCT (n = 482) found ACWR-guided management **did not reduce injuries**; and a 5,205-runner
   cohort found higher ACWR associated with *lower* injury rate.
2. **Fixed population thresholds** — no FMS-style cut-off, no "10% asymmetry" rule.
3. **Traffic-light banding as the primary analysis** — dichotomisation causes documented power
   loss and residual confounding, and binning choice alone produced 16–42 false discoveries per
   100 in simulation. Bands are a *display* convenience only (§11).

### 6.4 Verified behaviour (squats replay, full pipeline simulated at 60 Hz) **[V]**

| Phase | `m1` | `m2` | `m3` | `m4` | `m5` | **composite** |
|---|---|---|---|---|---|---|
| Standing still, 2–11 s (before work) | 0.0 | 0.0 | 0.0 | `null` | `null` | **0.0** |
| Squatting, 16–31 s | 44.1 | 52.6 | 7.0 | `null` | `null` | **35.2** |
| Standing still, 34–41 s (after work) | 0.6 | 0.7 | 13.7 | `null` | `null` | **7.5** |

The requested semantics hold: zero when fresh and idle, mid-scale during genuine work, and
settling onto the accumulated-dose floor (7.5) rather than collapsing to zero.

⚠️ **`m4` and `m5` are `null` in every row** — the log contains only 19.9 s of movement, below
their 60 s / 30 s warm-ups. This table therefore validates `m1`, `m2`, `m3` and the composite
**only**. See §11.1.

*Produced by the **shipped implementation** (`backend/ingest/biomech.py`, S1-T15) replaying
`example/squats.bin`, and asserted by `test_squats_replay_golden_values`. Earlier revisions of
this table came from S1-T14 prototypes that differed from the final code in the peak statistic
and the dose law; those numbers no longer reproduced and have been replaced. **Any future change
to §4's ranges or §5's definitions invalidates these numbers — regenerate them, never edit them
by hand.***

---

## 7. State, memory and sample context

`compute()` needs **more than the ~10 newest samples per tick**. Measured: peak dynamic accel
from a single 16.7 ms tick reads **3.55 m/s²** vs **8.29 m/s²** over 1 s — a 2.3× underestimate,
because a 10-sample window usually misses the impact entirely. **[V]**

**Two-level summary structure (required — not an optimisation detail).** Rather than buffering
1 s of raw derived samples (~600/limb), each tick reduces its ~10 new samples to **three
float32 summaries** (`p90 adyn`, `p90 jerk`, `mean adyn`), and only **60 of those summaries**
(= 1 s) are retained. `m1`/`m2` then take the **`max` over those 60 values**, not a percentile.

🚩 **The two levels use different statistics and the pairing is mandatory — see §5.1.** The `p90`
is *within* the tick, where it rejects single-sample artifacts; the aggregate *across* the ring is
`max`, because a percentile across the ring makes an isolated impact invisible (a 50 m/s² impact
moves ring-p90 by 0.000). *(An earlier revision of this paragraph said "a percentile over 60
values", contradicting §5.1. §5.1 is correct.)*

| Buffer | Length | Contents | Bytes/device |
|---|---|---|---|
| Per-tick summaries | 60 ticks = 1.0 s × 4 limbs | 3 × float32 summaries | **2,880 B** |
| Control window | 20 s | running sums of `adyn` per limb — **not** a buffer | ~0 |
| Filter state | — | LPF/baseline `zi`, last `a_vec`, last timestamp | ~0 |
| Session state | — | `dose`, `accL`, `accR`, `R_base`, counters, calibration | ~0 |

**Only derived scalar summaries are stored, never raw 6-axis frames.** Total live state is
**~3 KB/device**, ~15 KB for all 5 devices — 7× smaller than buffering the raw ring, and every
window longer than 1 s is an O(1) recursive accumulator, so nothing scales with window length.

### 7.1 Real-time feasibility — measured, not estimated

**Latency budget (algorithmic onset delay):** **[V]**

| Stage | Delay |
|---|---|
| 2× cascaded one-pole LP @ 75 Hz (group delay) | 4.24 ms |
| Gravity high-pass (τ = 0.35 s) | ~0 — removes DC, no delay at impact frequencies |
| Vector jerk (1-sample backward difference) | 1.67 ms |
| Tick quantisation @ 60 Hz | 16.67 ms |
| **Biomech total** | **≈ 22.6 ms** |
| *Jitter buffer (`JITTER_BUFFER_MS`, TRD §4)* | *50 ms — **dominates**, and is already in the pipeline* |

**The biomech model is not the latency bottleneck; the existing jitter buffer is.** End-to-end
wearable→browser is ~70–100 ms, of which biomech contributes under a quarter.

**Critical distinction — trailing windows do not delay onset.** `m1`/`m2` are *"peak over the
last 1 s"*. A new impact changes that value on the **very next tick** (16.7 ms). The 1 s window
governs how long a peak *persists*, not how long it takes to appear. That decay is a deliberate
peak-hold: without it a 5 ms impact would be invisible on a 60 Hz chart.

**Throughput — measured on the shipped implementation (S1-T15), 5 devices × 4 limbs @ 60 Hz:**
**[V]**

| Implementation stage | µs/tick (5 devices) | % of one core |
|---|---|---|
| First working version (`np.percentile`, per-limb loops, `np.median` ×5) | 5,321 | 31.9% |
| \+ vectorised per-tick summaries and EMA | 3,531 | 21.2% |
| \+ removed `np.median` from the dt path (see §3.6 note) | 2,625 | 15.7% |
| **\+ `_p90_axis0`, hand-rolled cross product, cached role/cal vectors** | **1,590** | **9.5%** |

**3.3× faster than the first working version, ~10× headroom.** Scaling is linear and flat per
device — 344 µs at 1 device, 331 at 3, 332 at 5 — which is what per-device batching predicts.

⚠️ **This is PER-DEVICE batching, not the cross-device batching earlier revisions of this section
assumed.** The ingest pipeline runs **one asyncio task per device** with independent, separately
reset tick epochs (`ticker.py`), so there is no instant at which every device's frames are in
scope; a single cross-device `lfilter` call would require replacing that scheduler. Measured
cost of the alternatives at 5 devices: per-limb 3,736 µs, **per-device 996 µs**, cross-device
213 µs. Per-device was chosen deliberately (user decision, S1-T15):

- 9.5% of one core at the 5-device ceiling leaves ~10× headroom, so the extra CPU buys nothing.
- Rewriting the scheduler would put the already-validated S1-T13 churn behaviour at risk.
- **Cross-device batching is arguably worse for latency isolation**: it computes all devices in
  one synchronous block, so every device's tick waits for the whole batch and one device's bad
  data blocks the rest. Per-device tasks stay independent.
- §7.2's "deterministic timing as devices connect/disconnect" rationale does not apply here —
  each device's ticker is already independent of the others.

Implementation rules that produced the numbers above, all of which S1-T15 follows:

1. **One `lfilter` call per filter stage per device**, over all 4 limbs × 3 axes at once
   (`axis=0`, state via `zi`). Per-limb calls cost ~3.8× more, dominated by Python call overhead
   rather than arithmetic.
2. **Never loop over samples in Python.** The recursive filters are the only sequential step;
   `lfilter` runs them in C and carries state across ticks exactly.
3. **Avoid `np.percentile`/`np.median` on short blocks.** For ~10 samples their cost is almost
   entirely dispatch overhead — `np.median` alone was 171 µs/tick and `np.percentile` 226 µs.
   `_p90_axis0` (a `np.partition` + lerp) is ~10× cheaper and numerically identical.

Vectorisation must not change the maths: `lfilter([α], [1, −(1−α)], x, zi=…)` **is** the one-pole
recursion, bit-for-bit. Filter coefficients are recomputed per batch from the **measured** Δt
(§3.6), so the rate-flexible requirement survives batching.

### 7.2 Fixed-slot batching — surviving fewer devices and mid-run faults

Batching (§7.1) is what makes this cheap, but a naive batch is shaped to the number of live
sensors — and `lfilter`'s state `zi` is shaped to the column count, so a sensor appearing or
disappearing would resize the state and destroy every filter's history. The design is therefore
**fixed-slot, scoped to one device**:

- Each device's session allocates a **permanent 4-slot matrix** (one per limb in `LIMB_MAP`),
  fixed for the life of the session. Slot order is the sorted limb names, so it is stable across
  restarts — which is also why the §7.4 snapshot keys calibration by limb name, never by index.
- Slots for absent limbs are **hold-last filled** — the slot's most recent real sample is
  repeated (§7.2.1). Limbs also arrive with *different* sample counts within the same tick, so
  short limbs are padded the same way and a per-limb `valid_n` keeps padded rows out of the
  summaries.
- A boolean `active[4]` mask marks which limbs had real data this tick. **Inactive limbs are
  excluded from all aggregation** (`m1`..`m5`); the §8 degradation ladder then applies exactly
  as specified.
- Devices beyond `MAX_DEVICES` (`.env`, default 5) are dropped and counted in
  `ingest:stats/global:dev_dropped`, never silently mixed into another device's stream.

**Per-device cost is flat; the total scales linearly** — measured on the shipped
implementation: **[V]**

| Active devices | µs/tick (total) | µs/device |
|---|---|---|
| 1 | 344 | 344 |
| 2 | 550 | 275 |
| 3 | 992 | 331 |
| 5 | 1,590 | 332 |

Adding a device adds ~330 µs of CPU, **not latency**: every device has its own asyncio task and
its own tick schedule, so device 1's tick is emitted without waiting for device 5. One core
saturates at roughly 50 devices — ten times the supported ceiling.

*(Earlier revisions specified a single cross-device 20-slot matrix. That is unimplementable
against the per-device ticker without replacing the scheduler; see the §7.1 note for the
measurement and the reasoning behind keeping devices isolated.)*

#### 7.2.1 Absent slots MUST be hold-last filled, never zero-filled

This is not a stylistic choice — zero-filling silently corrupts the gravity baseline. A sensor
that drops out for 3 s and returns while the athlete stands perfectly still: **[V]**

| Absent-slot fill | Baseline at reconnect | False `adyn` peak on return |
|---|---|---|
| **Zero** | 0.002 m/s² | **9.75 m/s²** — `m1` ≈ 85/100 out of nothing |
| **Hold-last** | 9.842 m/s² | **0.096 m/s²** — the true noise floor |

With zero-fill the baseline EMA decays to zero during the gap, so on return `|a| − base` = the
full 9.81 m/s² of gravity, and the dashboard shows a near-full-scale impact for a sensor that
merely reconnected.

**Hold-last needs no re-seeding, and re-seeding makes it worse.** Measured against a control slot
that stayed live throughout (`m1`-raw = 1.079): **[V]**

| Reconnect handling | Worst `m1`-raw after reconnect | Ratio vs. before fault |
|---|---|---|
| **Hold-last only** | **1.138** | **1.10× — within noise of the control slot** |
| Hold-last + filter re-seed + ring discard | 1.302 | 1.26× — *worse* |

The reason is a direct dividend of §3.3: the gravity baseline tracks **`|g|` = 9.81, which is
orientation-independent**. A held baseline therefore stays valid across the gap *and across a
remount* — the athlete can re-strap the sensor while it is off and the baseline is still right.
Re-seeding throws away a converged baseline and re-warms it, which costs more than it saves. An
axis-based model would have no such property and would need a full re-warm-up here.

*(Note: `zi` for the one-pole `y[n] = α·x[n] + (1−α)·y[n−1]` in scipy's transposed direct-form II
is `(1−α)·v`, not `v`. Getting this wrong injects the very transient it is meant to remove.)*

### 7.3 What needs history, and what does not

**Nothing in the live path touches the database, and maximum lookback in `ingest` is 1 second.**

| Component | Memory needed | Effective bandwidth | Responds to a change in |
|---|---|---|---|
| Low-pass / gravity baseline | O(1) filter state | — | ms |
| `m1` Impact | 1 s of summaries | 60 Hz onset, 1 s release | **1 tick (16.7 ms)** |
| `m2` Loading Rate | 1 s of summaries | 60 Hz onset, 1 s release | **1 tick (16.7 ms)** |
| `m3` Accumulated Load | O(1), 45 min half-life | ~0.0004 Hz | minutes |
| `m4` Movement Control | O(1), 20 s window | ~0.05 Hz | tens of seconds |
| `m5` L/R Balance | O(1), 5 min half-life | ~0.003 Hz | minutes |
| `composite` | none — algebraic | inherits the fastest input | **1 tick** |

**Honest framing of the 60 Hz stream.** `m1`, `m2` and `composite` genuinely carry ~60 Hz of
information and will look sharp and responsive. `m3`, `m4` and `m5` are physiologically slow —
they change over minutes and are *rendered* at 60 Hz as smooth curves, not sampled at 60 Hz of
independent information. **That is correct, not a defect:** fatigue and asymmetry do not change
in 16 ms, and a metric that appeared to would be measuring noise. The UI should not imply all
five channels are equally live; the slow three are best drawn as smooth trend lines.

**Hours, days and weeks are not this model's job.** They belong entirely to TimescaleDB's
`metrics_1m` continuous aggregate and the Stage-2 window/forecast endpoints (TRD §6) — a separate
read path that never touches the live path. If a multi-day load term is added later, use the
EWMA structure (τ ≈ 3.5 d acute, 14 d chronic) but **not** the acute:chronic ratio (§6.3).

### 7.4 Session-state snapshot to Redis — REQUIRED

Session state is in-memory, so without this an `ingest` restart silently resets a mid-session
athlete to zero accumulated load, drops the learned `R_base`, and discards calibration. All of it
is O(1) scalars, so persisting it is cheap. **Confirmed required by the user.**

**Snapshot contents (~200 B/device):**

| Field | Type | Notes |
|---|---|---|
| `dose` | float | `m3` accumulator |
| `accL`, `accR` | float | `m5` decaying load accumulators |
| `R_base`, `move_time_s` | float | `m4` baseline + the 60 s lock progress |
| `session_started_at`, `last_tick_at` | float (unix s) | drives the `SESSION_GAP_S` decision on restore |
| `cal[limb_name]` | 5 × float | per sensor: `k`, `gyro_bias[3]`, `sigma` (§3.8) |
| `cal_src[limb_name]` | str | `default` / `carried` / `measured` — calibration provenance (§3.8) |
| `schema_version` (`v`) | int | reject-and-restart on mismatch |

🚩 **Calibration MUST be keyed on the limb name (equivalently the sensor triple it is mapped
from), never on the slot index.** Slots are assigned dynamically on first packet (§7.2), so if devices reconnect
in a different order after a restart, slot-keyed calibration would silently apply the wrong
sensor's gain and bias — and since the corrections are small (0.5%), the result would look
plausible rather than obviously wrong. Slot index is an implementation detail of the batch layout
and must never appear in persisted state.

**Behaviour:**
- **Write:** one Redis key per device, `biomech:state:{device_id}`, rewritten **once per second**
  from the ticker (never per tick). Fire-and-forget, matching the existing `last_seen`/stats
  pattern (BACKEND_SCHEMA §4). A Redis write failure is counted and ignored — it must never stall
  the 60 Hz path.
- **Restore:** on startup, load each key and **apply the elapsed-time decay** for
  `now − last_tick_at` before use (`dose` and `accL`/`accR` have half-lives, so a restart during a
  long gap must not resurrect stale load). If `now − last_tick_at > SESSION_GAP_S`, discard and
  start a fresh session — identical to the normal gap rule (§7).
- **TTL:** `2 × SESSION_GAP_S`, so abandoned devices expire on their own. Redis persistence stays
  off (`appendonly no`) — this is a warm-restart convenience, not a durable store, and everything
  in it is re-derivable by simply continuing to measure.
- **Never snapshot:** the 1 s summary rings or filter state. Those re-warm in ≤1 s and are not
  worth the bytes; only the minute-and-longer accumulators need to survive.

⚠️ **Not a substitute for `quality`.** A restored session resumes accumulated load but the gap
itself is real — the ticker's existing `held`/`quality` machinery still reports it, so a trainer
can see that data was missing rather than seeing a seamless-looking line.

**Session boundary.** A new session (reset `dose`, `accL`, `accR`, `R_base`, counters) begins when
a device comes online after a gap exceeding `SESSION_GAP_S = 300 s` — deliberately **longer than
`OFFLINE_AFTER_S` (2 s)**, so a brief WiFi dropout or a rest between sets does not wipe
accumulated load. Ticks held during a short gap do not accumulate dose.

---

## 8. Degraded operation — fewer than 4 sensors

Unavailable primitives emit **`null`**; the composite renormalises `degradation` over available
terms so a device with fewer sensors is not systematically scored as lower-risk.

| Sensors present | `m1` | `m2` | `m3` | `m4` | `m5` | Composite |
|---|---|---|---|---|---|---|
| 4 (both legs, shank+thigh) | ✔ | ✔ | ✔ | ✔ | ✔ | full |
| 2 — one leg (shank+thigh) | ✔ | ✔ | ✔ | ✔ | `null` | over `m4`,`m3` |
| 2 — both shanks | ✔ | ✔ | ✔ | `null` | ✔ | over `m5`,`m3` |
| 2 — both thighs | ✔¹ | ✔ | ✔ | `null` | ✔ | over `m5`,`m3` |
| 1 — any | ✔¹ | ✔ | ✔ | `null` | `null` | `m3` only |
| 0 / no data | held | held | held | held | held | held |

¹ `m1` falls back to thigh, flagged `no_shank` — thigh impact is not the validated shank
surrogate. `quality` (TRD §4 step 8) already reflects missing sensors, so the UI can distinguish
"low risk" from "less data".

---

## 9. Display (`frontend/src/lib/metrics.ts`)

| ID | Display name | Short | Tooltip |
|---|---|---|---|
| `m1` | Impact | IMP | Peak shock magnitude reaching the lower leg |
| `m2` | Loading Rate | RATE | How abruptly load is being applied |
| `m3` | Accumulated Load | LOAD | Total mechanical work absorbed this session |
| `m4` | Movement Control | CTRL | Shock absorption vs. this athlete when fresh |
| `m5` | L/R Balance | BAL | Left/right load imbalance |
| `composite` | **Injury Risk** | RISK | Load applied vs. current capacity — a monitoring aid, not a prediction |

Bands (display only, §6.3): 0–30 low, 30–60 moderate, 60–80 elevated, 80–100 high.

**Rules:** `m4`/`m5` render **greyed when `null`** (warming up / sensors absent) — never as 0,
which would read as "perfect". Never show a directional claim ("your left leg is weaker") — §5.5.
Any individual flag must show the component panel that drove it (§2).

---

## 10. Interface changes (TRD §4 / BACKEND_SCHEMA §5)

`compute()`'s signature is **unchanged**. Extensions:

1. **`m` entries nullable** (§8, warm-up, saturation): `"m":[42.2, 48.3, 24.2, 0.0, null]`. The
   DDL already permits it (`m1..m5` nullable `REAL`); `metrics_1m`'s `avg()` skips NULLs.
2. **Range 0–100**, not the stub's 0–1 — frontend axis bounds must change with the model.
3. **`Metrics` gains `flags: frozenset[str]`** and **`raw: dict[str,float]`** — diagnostics only,
   published to `biomech:diag:{device_id}`, never written to `metrics`. `raw` carries the signed
   `R`, `R_base`, signed `usi_pct`, `dose`, `move_t`, `intensity`, `a_int`, `w_int`, `sat_frac`,
   `m1_lo`, `demand`, `degradation`, and the **per-tick noise weight `W`** — `W` is there
   specifically because a constant `1.0` is the visible signature of the §5.5 bug (weighting
   applied to the accumulators instead of per tick). Flag vocabulary:

   | Flag | Meaning |
   |---|---|
   | `warming_up` | `m4`/`m5` still inside their 60 s / 30 s warm-up (§5.4, §5.5). Only when the sensors those metrics need have actually streamed — see `degraded_sensors` |
   | `partial` | a required sensor went inactive mid-session; `m4`/`m5` frozen or `null` |
   | `no_shank` | `m1` fell back to thigh sensors |
   | `saturated` | >2.6% clipped samples; `m1`/`m2` suppressed (§3.7) |
   | `degraded_sensors` | device streaming <4 sensors (§8), **or** a sensor a metric requires was mapped but has never produced data (flat battery, bad strap). Both are "this value is never coming"; `warming_up` promises the opposite, so the two must not be confused. Tested against `ema_seen`, never against `LIMB_MAP` alone |
   | `uncalibrated` | at least one sensor running on default `k`/`bias`/`σ` — no history and no still window yet (§3.8) |
   | **`carried_over`** | **at least one sensor running last-known-good values from a PREVIOUS session (`biomech:cal:{dev}`), applied but not measured on this athlete today (§3.8)** |
   | `cal_failed` | a calibration attempt hit a validity guard and was rejected; last-known-good stands and detection continues |
   | **`unvalidated`** | **`m4`/`m5` — synthetic fixtures only, no real-data validation (§11.1). Set for the whole of stage 1.** |

4. **`ingest:stats` gains `sat_count`** per sensor (§3.7).
5. **`SESSION_GAP_S`** (default 300) is an **`.env` key**, added to TRD §7. *(Earlier revisions
   put it in `biomech.py`; it governs when an athlete's accumulated load resets, which is
   operational behaviour a deployment may need to tune, and the project rule is one-place config.
   `DOSE_EXPONENT`, half-lives and reference bounds stay file-local — those are model parameters
   that must not drift per deployment or the numbers stop being comparable.)*
6. **`state` gains a calibration block** (§3.8), per sensor — `k` (accel gain), `gyro_bias`
   (3-vector), `sigma` (accel noise), plus each sensor's provenance
   (`default`/`carried`/`measured`). Absent ⇒ defaults `1.0 / (0,0,0) / 0.035`.
   **`compute()`'s signature is unchanged: it performs still-detection and calibration itself**,
   in `state`, with no argument and no caller involvement. `calibrate(frames_window) ->
   dict[limb, Calibration] | None` remains alongside it as the batch form for a caller holding a
   complete window; it returns `None` on any validity-guard failure. Flags gain `uncalibrated`,
   `carried_over`, `cal_failed`. The carry-over key `biomech:cal:{device_id}` is
   read/written by ingest only (BACKEND_SCHEMA §4) — **no API route**.

---

## 11. Validation plan (S1-T15 tests)

**Golden values — squats replay**, all **measured** this session, with generous tolerances: **[V]**

| Check | Expected |
|---|---|
| Still, 2–11 s | `m1` < 2, `m2` < 2, `m3` = 0, composite < 2 |
| Squats, 16–31 s | `m1` 40–52 (44.1), `m2` 48–62 (52.6), `m3` rising monotonically to ~7 (7.0), composite 30–42 (35.2) |
| Still, 34–41 s | `m1` < 4, `m3` ≈ 13.7 (holds), composite ≈ 7.5 (**dose floor, not 0**) |
| Transmission `R` during squats (diagnostic `raw`, not `m4`) | 1.380 left, 1.382 right (agreement < 0.05) |
| `m4`, `m5` | **`null` in every row** — asserted explicitly, see §11.1 |

### 11.1 ⚠️ `m4` and `m5` ship WITHOUT real-data validation — deliberate, user-approved

`squats.bin` is the only capture that exists and it contains **19.9 s of movement**, below `m4`'s
60 s baseline lock and `m5`'s 30 s warm-up. **Neither metric emits a single value on it.** Two of
five primitives therefore have **no golden values from real hardware**, and the composite is
validated only in the regime where its `degradation` term reduces to `m3`.

**Decision (user, this session): ship them on synthetic fixtures and validate live in S1-T15
step 4.** This must be visible in the code, not just here:

- `biomech.py` carries a module-level comment naming `m4` and `m5` as synthetically-validated
  only, pointing at this section.
- Their `Metrics` entries carry the flag **`unvalidated`** for the whole of stage 1, so the
  `/debug` viewer and `/api/health` can surface it.
- `test_biomech.py` groups these tests under `class TestSyntheticOnly` with a docstring stating
  that passing does **not** demonstrate real-world correctness.

**Synthetic fixtures required** (each a generated frame stream, not a recording):

| Fixture | Asserts |
|---|---|
| 5 min symmetric movement | `m5` emits after 30 s and converges to ≈ 0 |
| 5 min with a **known 12% L/R load imbalance** | `m5` converges to 12% wUSI ⇒ ≈ 67 (±5) |
| 5 min with `R` ramped +25% after the baseline locks | `m4` locks `R_base` at 60 s, then rises to ≈ 50 (±5) |
| 5 min with `R` ramped **−25%** | `m4` also rises to ≈ 50 — proves direction-agnostic (§5.4) |
| One-sided sensor death at t = 90 s | `m5` freezes, then `null` + `partial`; **never** rises toward 100 |
| Shank death with thigh alive at t = 90 s | `m4` freezes, then `null` + `partial`; **never** pins at 100 |

**Exit condition for the gap:** during S1-T15 step 4, record **one ≥10-minute session with a
deliberate fatigue block** (e.g. a long set to near-failure, or a run with a hard finish) on real
wearables. Until that exists and §6.4 gains `m4`/`m5` rows from it, neither metric may be
presented to a trainer as a finding — only as a trend with the `unvalidated` flag visible.

**Synthetic unit tests** — these protect the design:

1. **Pure-rotation immunity** — stationary sensor at 30/90/180/360/720 °/s ⇒ `m1 = 0` (±0.5).
   The regression test for §3.3 and the single most important test in the file.
2. **Exact-jerk identity** — synthetic known world-frame jerk + arbitrary rotation ⇒
   `‖Δa/Δt + ω×a‖` recovers truth to <0.1 m/s³ at up to 1500 °/s (§3.4).
3. **Orientation invariance** — apply an arbitrary fixed rotation to every sample of the squats
   log; all six outputs **bit-identical**. Guards the whole no-orientation claim.
4. **Sample-rate independence** — decimate 600→300→150 Hz; `m3` within 5%; `m1` degrades
   gracefully, never jumps.
5. **Packet-loss robustness** — drop 30% of samples at random; `m3` within 10%.
6. **Gravity-only input** — constant 9.81 m/s² on any axis ⇒ all primitives zero.
7. **Free-fall** — must not produce a spurious `m1` peak (§3.3 fold-over).
8. **Saturation suppression** — a synthetic clipped impact ⇒ `m1`/`m2` `null` + `saturated` flag.
9. **Degradation ladder** — every row of §8 gives the right `null` pattern, and the composite
   does not fall merely because sensors are missing.
10. **Held ticks** — empty `frames` repeats previous `Metrics`, accumulates no dose.
11. **Session reset** — a >300 s gap zeroes `dose`/`R_base`; a 3 s gap does not.
12. **Calibration correctness** — synthetic sensors given known gain/bias errors ⇒ the running
    sums recover them within 0.1%, and `m5` bias caused by a deliberate 3% L/R gain mismatch
    drops to <0.5 points after correction. *(The 3% is applied as ±1.5% per side: a single
    sensor reading 3% off gravity is indistinguishable from a faulty one and is correctly
    refused by the acceptance guard — which test 13 asserts.)*
13. **Calibration rejection** — a "still" window containing movement (`|ω|` > 5 °/s), or a
    sensor reading `|a|` = 8.0 m/s², must leave the previous values in place: `calibrate()`
    returns `None`, and automatic detection never accepts the window.
14. **Uncalibrated path** — the full golden-value suite (above) must pass with **no calibration
    applied**; calibration is automatic but must never be load-bearing for correctness, and a
    device with no history that never stands still has to keep working on defaults.
15. **Throughput guard** (`pytest -k bench`) — fully batched `compute()` for 5 devices must stay
    **under 3 ms per tick** (measured **1,590 µs**, §7.1), asserting the batching rules survive
    refactoring. The guard sits ~1.9× above the measurement because it runs alongside the rest
    of the suite; the regression it exists for — reverting to per-limb `lfilter` calls — costs
    ~3.8× and lands at ~6 ms. `test_biomech.py` must assert this same 3 ms.
16. **Latency guard** — a synthetic step impact must move `m1` on the **next tick**, proving
    trailing windows delay release, not onset (§7.1).
17. **Variable device count** — run with 1, 2, 3, 5 devices; all must produce correct metrics,
    and **per-DEVICE** time must stay flat (~330 µs) while the total scales linearly and stays
    well inside the tick budget (§7.2). *(Earlier revisions asserted a flat TOTAL; that holds
    only for cross-device batching, which this build deliberately does not use.)*
18. **Zero-fill regression guard** — deliberately zero-fill an absent slot and assert the
    reconnect produces the ~9.75 m/s² phantom peak; then assert hold-last does not. This test
    exists to stop anyone "simplifying" the fill rule later (§7.2.1). **High value.**
19. **Mid-run sensor fault** — kill one sensor of a 3-device set for 2 s: the affected device
    falls to its §8 degraded row, **other devices are bit-identically unaffected**, and on
    reconnect `m1` returns within noise of a slot that stayed live (≤1.2× — measured 1.10×).
20. **Slot exhaustion** — a 6th device with `MAX_DEVICES = 5` is dropped and counted, never
    mixed into an existing slot.
21. **State snapshot round-trip** — snapshot mid-session, restart, restore: `dose`/`accL`/`accR`
    resume with the correct elapsed decay applied, and `R_base` survives. A restore with
    `now − last_tick_at > SESSION_GAP_S` must discard and start fresh (§7.4).
22. **Calibration key stability** — snapshot with devices in one slot order, restore with the
    order permuted; each sensor must receive **its own** `k`/`gyro_bias`/`sigma` (§7.4).
23. **Radians guard** — feed a known `ω` and `a`; assert `m2` matches the closed-form
    `‖ȧ + ω_rad×a‖`. A °/s implementation is off by 57.3× and this test must catch it (§3.5).
24. **Ring-percentile regression guard** — assert that an isolated single-tick 50 m/s² impact
    moves `m1`, and separately that a *ring-percentile* implementation would not. Stops anyone
    reintroducing the §5.1 bug for speed. **High value.**
25. **Noise-weighting efficacy** — assert `W < 0.7` when per-tick load is at the noise floor and
    `W > 0.99` during movement; a `W ≡ 1` implementation (weighting applied to the accumulator
    instead of per tick) must fail (§5.5).
26. **Still-window detection** — 15 s of stillness calibrates with no trainer action, and lands
    at ~13 s (3 s discard + 10 s window). A window containing movement, one containing rotation
    above 5 °/s, a sensor stuck at `|a|` = 8.0, and ten *discontinuous* still seconds must all
    leave the sensor uncalibrated (§3.8).
27. **Carry-over** — a session that is movement from the first tick (so no window can land) still
    runs on last-known-good values, flagged `carried_over` and never `uncalibrated`; a later
    still window replaces them and clears the flag; a session reset demotes `measured` back to
    `carried` rather than to defaults (§3.8).

**Live iteration (S1-T15 step 4):** on `/debug` with real wearables, confirm standing still ≈ 0,
walking clearly above zero, and the ordering `jump > run > squat > walk > still` on `m1`. Any
reference bound in §4 that puts a real activity outside 10–90 gets retuned and §4 updated per
S1-T15 step 5.

---

## 12. What changed from the stale model, and why

| Stale model | This spec | Reason |
|---|---|---|
| Axial accel via `shank_axial_axis="z"` | Resultant magnitude | Declared axis is **empirically wrong** — gravity is on `ay` (§3.2) |
| Per-axis gravity removal (VeDBA-style) | Scalar `\|a\|` high-pass | Per-axis fabricates **7.3 m/s²** of fake impact at squat rotation rates (§3.3) |
| — | **Exact `\|ȧ + ω×a\|` jerk** | Gravity-free and rotation-invariant *identically*, from measured signals (§3.4) |
| 4 primitives + many indexes | 5 primitives + 1 composite | Fixed by schema; spans 5 orthogonal constructs |
| Raw SI units stored | 0–100 everywhere | User requirement |
| Linear normalisation | Log for magnitudes, linear for ratios | 50× dynamic range lifting↔running (§4) |
| Mean-of-normalised composite | Load-vs-capacity, non-linear | A linear mean is fully compensatory and cannot express interaction (§6.2) |
| 4th-order Butterworth @ 50 Hz, fixed 640 Hz | Rate-adaptive one-pole cascade @ 75 Hz | Input rate is not fixed; 50 Hz loses 25–64% of peak jerk (§3.6) |
| Linear dose | Power-law dose (exponent 3) | Bone loading is `magnitude^m × cycles`, m ≈ 2–4 (§5.3) |
| Classic Symmetry Index | wUSI | SI fails 5-axiom benchmark; wUSI handles the noise floor (§5.5) |
| Windows/forecasts inside the model | DB's job (continuous aggregates) | Stage-2 architecture (TRD §6) |
| Verified physical mounting axes required | Calibration **automatic**; system correct without it | Falls out of orientation-free design. Automatic still-detection measures gain/bias/noise only — never orientation — and carries last-known-good across sessions (§3.8) |

Retained: mean-not-sum for dose, sample-rate independence, and the shank/thigh + left/right
topology.

---

## 13. Open items

| # | Item | Needed by | Status |
|---|---|---|---|
| 1 | ~~`m5` full scale~~ | — | **CLOSED** — 18% wUSI, from Delgado-García 2025 (§5.5) |
| 2 | 🚩 **Accelerometer range.** ±16 g will clip on the **shank during running/jumping**; literature recommends ≥±32 g. Fine for thigh and all strength training. | before running trials | Saturation counter + metric suppression implemented (§3.7); hardware change is a user/firmware decision |
| 3 | Reference bounds (§4), composite weights (§6), `DOSE_EXPONENT`, and the borrowed **2.6% saturation threshold** (§3.7) — all provisional | post-MVP | Calibrate against trial data |
| 10 | **`m4`/`m5` have no real-data validation** — the only capture has 19.9 s of movement vs their 60 s / 30 s warm-ups. Shipping on synthetic fixtures by user decision; flagged `unvalidated` in code and surfaced in `/api/health` | **S1-T15 step 4** | §11.1 — needs one ≥10-min session with a deliberate fatigue block to close |
| 4 | `m4` direction — is transmission drift up or down under fatigue for *this* population? Signed `R` is retained in `raw` to answer this | post-MVP | Currently direction-agnostic (§5.4) |
| 5 | UI claims language (§2) — "Injury Risk" as a product name vs. the evidence on composite validity | stage 3 frontend session | Flagged for your decision |
| 6 | Confirm no device ships a different full-scale range | before multi-device trials | ±16 g / ±2000 °/s assumed (user: **keeping ±16 g**) |
| 7 | ~~Session-state persistence~~ | — | **CLOSED — user approved.** Specified as required in §7.4 (1 Hz Redis snapshot, ~200 B/device, decay applied on restore) |
| 8 | ~~Calibration UX — who triggers it, and how the athlete is told to stand still~~ | — | **CLOSED — user decision, supersedes the deferral.** Nobody triggers it: automatic still-detection, every session, with last-known-good carried across sessions (§3.8). The UI's only job is to render `uncalibrated` / `carried_over` / `cal_failed` and mark the step change |
| 9 | Slot-table sizing: `MAX_DEVICES` (5) is fixed at startup (§7.2). Raising it needs an ingest restart — acceptable for MVP | post-MVP | Devices beyond the cap are dropped and counted |

### References

Alves 2020 *Front Bioeng Biotechnol* 8:579511 (wUSI) · Bahr 2016 *BJSM* 50:776 (screening) ·
Bertelsen 2017 *SJMSS* 27:1170 · Bird 2023 *Front Physiol* 14:1088813 (composite AUC) ·
Bittencourt 2016 *BJSM* 50:1309 · Brice 2020 (squat ω decline) · Bullock 2022 *Sports Med*
52:2469 (204 models) · Chan 2023 *Sensors* 23:4609 (±16 g clipping) · Crenna 2021 *Sensors*
21:4580 (differentiation) · Delgado-García 2025 *Bioengineering* 12:294 (**tibial asymmetry
9→25%**) · Encarnación-Martínez 2022 *Sensors* 22:3786 · Hennig 1993 *JAB* 9:306 ·
Jämsä 2011 *Front Physiol* 2:73 (DIS, 4 g / 100 g/s thresholds) · Kalkhoven 2026 *Sports Med*
(load-capacity formalism) · Kiernan 2018 *J Biomech* 73:201 · Kiernan 2026 *J Biomech* 196:112955 ·
Kikumoto 2026 *IJSPT* (FMS AUC 0.52) · Malisoux 2024 *BMJ Open SEM* 10:e001787 (**asymmetry
does not predict injury, n=836**) · Marotta 2022 *Sensors* 22:3008 (fatigue review) ·
Matijevich 2019 *PLoS One* 14:e0210000 (**PTA ≠ bone load**) · Mitschke 2018 *Sensors* 18:130
(±32 g) · Pattin 1996 *J Biomech* · Sarantos 2025 *Sensors* 25:2656 (**shank↔thigh, resultant
recommended**) · Sheerin 2019 *Gait Posture* 67:12 (filtering) · Shorten & Winslow 1992 *IJSB*
8:288 · Tenforde 2020 *PM&R* 12:679 (axial vs resultant) · Van den Berghe 2019 *J Biomech* 86:238
(**resultant more reliable between sessions**) · Whalen 1988 *J Biomech* 21:825 (m=4) ·
Windt & Gabbett 2017 *BJSM* 51:428 · Zandbergen 2023 *Sports Biomech*
