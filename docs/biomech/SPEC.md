# Biomech SPEC — 5 primitives + composite injury risk

| | |
|---|---|
| Status | **Implemented and shipped** (S1-T15) in `backend/ingest/biomech.py`; revised in place as the model changed. |
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
the measured **~640 Hz** input rate and passes through untouched.

*(Note 2026-08-02: the measured input rate is **640 Hz**, not the 600 Hz earlier revisions
assumed — `EXPECTED_INPUT_HZ` was corrected the same day. **600 Hz survives only as
`NOMINAL_INPUT_HZ`**, the fallback `dt` used when a tick arrives with no timestamps at all; the
live path always differences real timestamps.)*

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
peak jerk, and sits at **~`fs/8.5`** (the measured **640 Hz** input, note 2026-08-02) —
comfortably below Nyquist and below documented accelerometer/mount resonances (250–1000 Hz).

**Filter type — one-pole cascade, not Butterworth.** A fixed-coefficient IIR assumes a fixed
sample rate; TRD §4 requires rate-flexible input. A cascaded one-pole with
`α = 1 − exp(−Δt/τ)` recomputed from **measured** Δt is rate-correct at any input rate, trading a
gentler roll-off for that robustness. **No `filtfilt`** — non-causal, forbidden by §1.4. **[E]**

Jerk is computed from the **low-passed** vector: raw ~640 Hz differentiation has noise
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
(b) above a saturated fraction of 2.6%, **report `m1`/`m2` as marked LOWER BOUNDS** and set the
`saturated` flag, so the UI renders them as "≥ x".

⚠️ **Changed 2026-08-03 (user decision): saturation no longer suppresses to `null`.** The
hardware cannot be changed, and it clips *inside ordinary athletic movement*. Measured through
this pipeline: dynamic acceleration tops out near **147 m/s²**, so **35 g, 42 g, 60 g and 100 g
landings all read `m1` = 75.2** — identical. Suppressing removed Impact and Loading Rate on ~2%
of ticks at 27 g PTA and ~7% at 42 g, i.e. exactly when load was highest, and took the
composite's demand term with them. **[V]**

Reporting a marked floor keeps the metrics monotonic (a harder landing never reads *lower*) and
keeps the composite alive; it only stops them being exact. `saturated` therefore now means
precisely *"these are lower bounds"* and fires at the 2.6% threshold — not on any single clipped
sample, which made the flag near-permanent during hard work. The raw fraction stays in
`raw.sat_frac`.

⚠️ *The 2.6% figure is borrowed, not derived.* It comes from a **gyroscope** clipping study on
**foot-mounted** running sensors (cumulative error stays <5% below that fraction) and is applied
here to the **accelerometer** on the shank. The transfer is plausible — both are "how much
clipping before the aggregate is untrustworthy" — but it is **not validated for this use**
(open item 3). Clipping also destroys jerk (a flat top has zero derivative). **±16 g is fine for
thigh sensors and all strength training, but marginal on the shank for running and jumping.**
Gyro ±2000 °/s is adequate everywhere except kicking. **[L]**

### 3.7.1 Above the clipping point, rotation is the only channel left **[V]**

Once the accelerometer clips, `m1` and `m2` cannot distinguish a fast benign movement from a
fast, violently rotating one — and off-axis (multiplanar) loading is precisely the injurious
pattern. Measured at a fixed 35 g landing while sweeping shank angular rate 100 → 1900 °/s:

| shank °/s | `m1` | `m2` | rotational score | composite |
|---|---|---|---|---|
| 100 | 75.4 | 58.4 | 48.9 | 49.8 |
| 600 | 75.4 | 58.5 | 80.3 | 57.0 |
| 1900 | 75.4 | 58.6 | 100.0 | 66.8 |

`m1`/`m2` are **completely flat**; rotation climbs across the whole range. So when `saturated`,
rotation claims a share (`ROT_ESCALATION`) of the demand headroom `m1` can no longer reach:

```
if saturated:  demand += (100 − demand) · ROT_ESCALATION · rot/100
```

🚩 **Gated on `saturated`, deliberately.** Unsaturated impact discriminates perfectly well on its
own, and *rotation alone must never read as risk* — a firm kick through the air is nearly pure
rotation with modest impact, and an ungated rotational term would inflate exactly that case.
`test_rotation_alone_does_not_inflate_demand` pins it.

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
| Acceptance, per tick | `mean\|ω\| < 5 °/s` **and** `\|mean\|a\| − 9.81\| < 6%` — the rejection guards below, used as the acceptance test |
| Window | **10 s of *continuous* stillness**; any failing tick resets the accumulation to zero |
| Scope | **Per sensor, independent** — sensors settle at different moments and calibrate at different moments |

The guards run on the **calibrated** magnitudes — what the pipeline actually carries. For a
sensor on defaults (`k = 1`) that is exactly the raw test; for one already running carried-over
values it tests the *residual* error, which is what lets carry-over be refined instead of
locking a corrected sensor out of ever measuring again. Measured corrections **compose** with
what is already applied (`k_total = k_applied · 9.81 / mean|a_calibrated|`).

**Running sums, never samples.** 10 s × 640 Hz × 4 sensors is ~2 MB per device to produce three
scalars, and all three are *exact* functions of `Σ|a|`, `Σω` and `Σ|a|²`:
`k = 9.81/mean|a|`, `gyro_bias = mean(ω)`, `σ = √(E|a|² − (E|a|)²)`. Only the sums are kept.

**Stand still, not "some motions."** All three products come from stillness; a motion routine
would add nothing and could not be standardised across running vs lifting users.

**Start calibrated, then refine (`biomech:cal:{device_id}`, BACKEND_SCHEMA §4).** On session
start the device's **last-known-good** calibration is loaded from a dedicated Redis key with a
TTL of **30 days** (`CAL_CARRY_TTL_S`), keyed on **limb name** and never on slot index, and
applied immediately — then
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
- `|mean(|a|) − 9.81| > 6%` ⇒ sensor faulty (the gyro is the movement authority) ⇒ **reject,
  keep defaults**. Was 2% until 2026-08-04, which contradicted the k range below: a motionless
  sensor 2–5% off gravity is *exactly* what calibration corrects, yet it failed acceptance every
  tick, never accumulated a window, and was branded `cal_failed` at 20 s — observed on real
  hardware as "held still 30–40 s, never calibrated, ran on carried values". The guard must be
  wider than the correctable k range; the k guard stays the decisive validator.
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

**Held ticks age the window, they do not pause it.** `compute()` returns early when no limb
produced samples at all. That early return must still advance `cal_gap`: a half-built window
surviving a whole-device dropout of arbitrary length and then resuming would mean the "10 s of
*continuous* stillness" guarantee was never actually checked. The real link drops packets in
bursts, so this is the common case, not a corner one.

- On rejection: flag `cal_failed`, keep last-known-good (or defaults), **clear the window and keep
  seeking**. There is nothing for the UI to retry: detection never stops.

**What calibration must NOT attempt:** orientation, axis alignment, or limb assignment. Those are
forbidden by §1.1 and are exactly the failure mode of the stale model (§3.2).

**Persistence.** Accel gain is stable across sessions; gyro bias drifts with temperature.
Recompute per session when a still window lands, and carry the last-known-good values as
fallback. Two keys, deliberately: the §7.4 snapshot (same session, TTL `2×SESSION_GAP_S`) and
`biomech:cal:{device_id}` (across sessions, TTL **30 days**, `CAL_CARRY_TTL_S`). A new session **demotes** its measured
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
representative of the actual activity, so it self-learns from movement (§5.4) — **a minimum of
120 s of it**: 60 s of settle (`M4_SETTLE_S`) and then 60 s within one intensity band
(`M4_BASELINE_LOCK_S`).
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
| `m1` Impact | **`max(6.0, 5σ)`** m/s² | **400 m/s²** | **`lo` is noise-adaptive** — derived from the per-sensor `σ` that calibration already measures (§3.8), so a noisier sensor cannot read m1 > 0 at rest. **Re-anchored 2026-08-03** (`M1_LO_FLOOR = 6.0`, `M1_HI = 400.0`, biomech.py): a 2.0 floor put an ordinary *walk* at 40/100, and a 150 m/s² ceiling is **exceeded by real movement**, so jumping, landing, kicking and hard deceleration all censored at exactly 100 and became indistinguishable. `hi` = 400 m/s² ≈ 41 g covers vertical jumping (~42 g) and is deliberately **2.5× the ±16 g per-axis full scale** — a resultant magnitude can exceed any single axis's range, and the ceiling must sit above the hardest real impact rather than at the sensor limit |
| `m2` Loading Rate | **2 500 m/s³** | **80 000 m/s³** | **Re-anchored 2026-08-03** (`M2_LO, M2_HI = 2_500.0, 80_000.0`, biomech.py). The previous `lo` of 800 was justified by the osteogenic jerk threshold (981 m/s³, Jämsä 2011) but sat *below ordinary walking* (~3 400 m/s³), so everyday movement never read 0; and 30 000 was exceeded by a small jump. The current `lo` sits at the bottom of the published running-impact jerk band (2 500–7 800 m/s³) **[L]** |

| `m3` Accumulated Load | **0.03** | **60** (dose·min) | §5.3. One dose-minute is **one minute of hard-training equivalent**, so the ceiling is a full hard hour. **Floor cut 0.5 → 0.03 on 2026-08-03** (`M3_LO`, biomech.py): 60 s of running accumulates only ~0.31 dose-minutes and a set of bodyweight squats ~0.005, so against a 0.5 floor `m3` read exactly **0 for everything tested**. 0.03 ≈ 2 s of hard-training equivalent **[V]** |
| **`ω` term (inside `m3`)** | **5 °/s** | **1500 °/s** | **Was missing entirely — `m3` was unimplementable.** `lo` above measured still-standing mean `\|ω\|` = 1.6 °/s; squat mean = 82 °/s → score 49 **[V]**; `hi` covers sprint thigh (792 °/s) and inferred shank (1200–1900 °/s) **[L]** |
| `m4` Movement Control | 0 | +100% tremor fraction vs own fresh baseline, per intensity band | §5.4 |
| `m5` L/R Balance | −100 | ±18% wUSI — **SIGNED**: + = left-dominant, − = right | §5.5 — literature-derived magnitude; sign is a within-session readout (user decision 2026-08-03) |

Reference bounds are **constants in `biomech.py`, not `.env` keys** — model parameters, not
deployment wiring (TRD §7 permits file-local tuning constants). They are **provisional starting
points requiring calibration against trial data**, not validated clinical cut-offs.

⚠️ *This table was stale until 2026-08-03 and contradicted the shipped code: it showed the
pre-retune `m1` floor of 0.15 and an `m2` range of 120–12 000, and its `m2` justification argued
against the value actually in `biomech.py`. Corrected from the constants and their in-code
rationale; the `m3` row was re-anchored again the same day with the §5.3 dose-law fix.*

### 4.1 The activity ladder — what the scale reads for real activities **[V]**

Measured 2026-08-03 by driving synthetic gait through the shipped `compute()`, with amplitudes
set from published **resultant** peak tibial acceleration (walk 2.7–3.7 g, jog ~5 g, run 8–12 g,
sprint ~20 g, drop landing ~27 g) **[L]**. The generator is calibrated by the fact that it
reproduced the previously reported live behaviour almost exactly (slow walk 26, light jog 55)
before the §5.3/§6.1 corrections.

🚩 **The `composite` column below is STALE — it is floor arithmetic** (`0.50·m3 + acute`), taken
before §6.1a removed the dose floor. Every value in it is reproducible *only* under the old
model: the sprint row computes to ≈**77** under the shipped composite, not 55.6, and brisk walk
to ≈**0.1**, not 6.0. The `m1`/`m2`/`m3`/`demand` columns are current and still useful — the
soft-max reproduces the demand column exactly. **The live ladder is the one in §6.1a**
(squats 1.3, walk 2.6, jog 6.0, kick 8.4, single-leg landing 17, jump 21 fresh / ~33 fatigued),
which is what the current constants were fitted against.

| Activity | `m1` | `m2` | `m3` | `m4` | demand | ~~composite~~ *(pre-6.1a, floor model)* |
|---|---|---|---|---|---|---|
| Standing still | 0 | 0 | 0 | – | 0 | ~~0.0~~ |
| Slow walk | 10 | 0 | 0 | 7 | 7 | ~~0.0~~ |
| Brisk walk | 18 | 0 | 12 | – | 12 | ~~6.0~~ |
| Light jog | 31 | 0 | 42 | 4 | 20 | ~~21.2~~ |
| Steady run | 44 | 16 | 56 | 5 | 33 | ~~29.7~~ |
| Hard run | 55 | 29 | 67 | 12 | 45 | ~~38.6~~ |
| Sprint | 68 | 45 | 86 | 14 | 59 | ~~55.6~~ |
| Landing work (sustained) | 74 | 54 | 68 | 29 | 59 | ~~51.9~~ |

Against the reported worn session, where the composite read ~5 for a medium walk, 50–60 for a
light jog and **100** for running, jumping, landing, deceleration, change of direction and
kicking. **[V]**

⚠️ **A single discrete event is not the same as sustained work at that intensity**, and the row
above is the sustained case. Measured separately for one landing followed by rest: peak composite
**4.0**, because `m3` never accumulates. *(The follow-on figures — "ten seconds of landings reads
16.5, sixty seconds 42.0" — were floor arithmetic on an accumulating `m3` and no longer hold;
under the capacity model a rested athlete's composite decays to 0 as soon as demand does.)*
The reported expectation of ≤30 for "a single-leg landing from a two-leg jump" is the 4.0 case.

What moved the ladder, in order of size: the `capacity` decoupling (§6.1), the `m1`/`m2`
re-anchoring (§4), the dose-law correction (§5.3), the exposure-based demand (§6.1) and the `m4`
rebuild (§5.4).

---

## 5. The five primitives

All windows are trailing and causal. `PEAK_WINDOW_TICKS = 60` ticks (= 1.0 s at 60 Hz);
`MOVE_GATE_MS2 = 0.10 m/s²` on tick-mean
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

Shank-preferred (the validated site for impact surrogates); with no shank mapped it falls back to
**ALL mapped limbs**, not to the thighs specifically — the shipped `impact_i` is
`shank_i if any shank else arange(n)` — flagged
`no_shank`. *(Corrected 2026-08-06; on a 4-sensor rig the two are the same set only because the
non-shank slots are the thighs.)* Above 2.6% saturation it is reported as a marked LOWER BOUND
rather than
`null` (§3.7) — the +-16 g part clips inside real athletic movement.

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
100 g/s ≈ 981 m/s³** (Jämsä 2011, hip-mounted). **[L]** Also: at our ~640 Hz only ~60–90% of true
peak jerk is recovered (peak jerk is ~5× more sample-rate-sensitive than peak acceleration), so
**`m2` is meaningful as a within-athlete relative trend, not an absolute physical value.** Pin
`fs`, cutoff and differentiation scheme; never compare across configurations.

### 5.3 `m3` — Accumulated Load

Mechanical intensity, then **power-law weighted** accumulation with decay:

```
load_ratio  = max( adyn_mean / A_DOSE_REF , wmag_mean / W_DOSE_REF )   physical, unbounded
dose_fast  ← dose_fast · 2^(−Δt / 15 min)                              always
dose_slow  ← dose_slow · 2^(−Δt / 90 min)                              always
if moving:  inc = load_ratio^DOSE_EXPONENT · Δt/60
            f   = clamp(load_ratio, 0, 1)²          share filed to the SLOW pool
            dose_slow += inc·f ;  dose_fast += inc·(1−f)
raw₃        = dose_fast + dose_slow

🚩 **TWO POOLS (2026-08-03).** How long a dose lingers must depend on how it was
**earned**, not on what the athlete is doing now. A single half-life scaled by *recent* activity
got that backwards: two minutes after a sprint the recent-load memory had faded, so the sprint's
dose then shed at the easy rate. Each increment is now filed to a pool at the moment it is
earned and keeps that half-life for good, which gives the requested behaviour directly — a short
hard effort climbs fast (cubic weighting), reaches a higher amplitude and **lingers**; a long
easy effort climbs slowly, reaches a lower amplitude and **clears quickly**. The split is
squared so it is sharp: hard running (`load_ratio` 1) is all slow, an easy jog (~0.6) files 36%,
a walk (~0.2) files 4%.
```

🚩 **The power law acts on the PHYSICAL load ratio, not on a 0–100 score — corrected
2026-08-03.** It was `(intensity/100)^3` where `intensity` was itself a `log_score`. But Whalen's
exponent applies to the stress `σ`, not to a log-compressed score of it, and log-compressing
first destroys precisely the magnitude weighting the exponent exists to apply. Measured
consequence: an easy walk scored `intensity` 57 against hard running's ~80, so it accumulated
dose at `(0.57/0.80)³ = 36%` of the hard-running rate when the physical loads differ by more than
an order of magnitude. 90 s of slow walking reached `m3 = 30`, putting a 15-point floor under the
composite and making a walk read as real injury risk. On the physical ratio the same walk
accumulates **14× less**, and the walk↔hard-run rate separation goes from 2.8× to ~90×. **[V]**

`A_DOSE_REF = 7.4 m/s²` and `W_DOSE_REF = 540 °/s` are the sustained **cube-mean** tick values of
hard running, measured on the §4.1 ladder. Cube-mean (`E[x³]^⅓`), not median: the dose integrates
every tick and the cubing lets impact ticks dominate, so a median understates the driving value
several-fold. Anchoring there gives `dose` an interpretable unit — **one dose-minute is one
minute of hard-training equivalent**, so `M3_HI = 60` is a full hard hour.

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
  minutes. *(🚩 This bullet's framing predates §6.1a: there is no dose **floor** any more.
  Read it as "a brief session accumulates little dose, so `degradation` barely moves and
  capacity is barely reduced" — the visible effect is small either way, but it now arrives
  through capacity, not through an additive baseline.)* A linear or squared dose would make
  the effect more visible at the cost of the
  magnitude-weighting the bone literature is unambiguous about. **User decision: trust the
  literature.** **[V] + [L]**
- **`DOSE_HALFLIFE_S = 15 min` (fast pool) / `DOSE_HALFLIFE_SLOW_S = 90 min` (slow pool)** —
  session-scale fatigue residue, split by how the dose was *earned* (see the two-pool block
  above): easy work sheds quickly, hard work lingers. Multi-day load is **not** this
  metric's job: it belongs to the TimescaleDB continuous aggregates (TRD §6). If a multi-day
  term is added later, use the EWMA structure (τ ≈ 3.5 d acute, 14 d chronic) — **but not the
  acute:chronic *ratio***, see §6.3. **[L]**

### 5.4 `m4` — Movement Control

🚩 **REPLACED 2026-08-03. `m4` is now a TREMOR INDEX, not a shock-transmission ratio.**

The worn protocol destroyed the ratio form. It was **null on 100% of ticks during jumps**, 70%
during single-leg landings and 61% during rest — it needed a matched shank *and* thigh on the
same leg with the shank above the move gate, so it vanished exactly when movement got
interesting. It read **highest during kicks (41) and limping (40)**, neither of which is fatigue.
And it went **6 → 4 during and after squats to failure**: it *fell* as the athlete fatigued.
**[V]**

The intent — well controlled when rested, losing control with fatigue through tremor, weakness
and having to work harder for the same movement — is unchanged. The tremor band is the
directly-evidenced signal for it: the 4–12 Hz power fraction is an established unitless index
that scales across tasks and individuals, it is scale-free (so it survives any future change to
the m1/m2 normalisation), and it needs **one** streaming limb rather than a matched pair. **[L]**

Implementation is three extra one-pole low-passes on `amag` — a band-pass is the difference of
two low-passes, and the 0.5 Hz edge is already the gravity baseline.

Two behaviour changes follow, both asserted in the suite:
- **m4 is available on any limb**, so §8's ladder gains it for the both-shanks and single-sensor
  rows.
- **m4 is DIRECTIONAL.** More tremor than fresh is a loss of control; *less* is not a problem and
  no longer scores. The old `|·|` existed because the literature does not fix the sign of
  shock-attenuation drift; it *does* fix the sign of tremor.

m4 **freezes when the active limb set differs from the set its baseline was learned on** — the
fraction is averaged over live limbs, so losing one shifts it against a baseline measured on a
different set (**+33 points** when one sensor died). Same class of guard as before; the
explosion it protected against is gone.

*The superseded definition, for reference:* shock **transmission ratio** between shank and thigh
of the same leg:
```
R = mean(adyn, thigh, trailing 20 s) / mean(adyn, shank, trailing 20 s)
```
Measured squat `R` = **1.380 left, 1.382 right** — agreement to 0.002 across limbs, confirming
`R` is a real, repeatable quantity. **[V]** But it is *amplification* (>1) in squatting where
running gives *attenuation* (<1), so no absolute 0–100 scale is honest across both. Hence
baseline-relative, per user decision:

```
tremor    = mean power 4–12 Hz / mean power 0.5–20 Hz, over live limbs
band      = intensity band of the current shank EMA |a_dyn|   (M4_BAND_EDGES)
base[b]   = time-weighted MEAN of `tremor` over M4_BASELINE_LOCK_S = 60 s of movement IN BAND b,
            learned only after M4_SETTLE_S = 60 s of movement, then locked (with the limb mask)
            ⇒ the MINIMUM to a first m4 value is 120 s of movement, not 60
m4        = 100 · clamp( max(tremor/base[band] − 1, 0) / 1.0 , 0, 1 )
```

🚩 **HISTORICAL — the `|·|` no longer applies.** The block below argued m4 must be
direction-agnostic, and that was right *for the shock-transmission ratio*, whose sign the
literature does not fix. **The tremor index replaced it on 2026-08-03 and IS directional**
(`drift = tremor/base − 1`, clamped at 0): more tremor than fresh is a loss of control, less is
not a problem and scores 0. `raw.R` is likewise now an unsigned tremor fraction, not a signed
ratio. Kept for the literature review it contains. My first draft scored
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

🚩 **HISTORICAL — superseded by the tremor index.** The freeze rule below protected the
*transmission ratio* from a divide-by-near-zero when a shank dropped out. The tremor index needs
only **one** streaming limb and has no such divide, so that explosion cannot occur. What the code
freezes on now is a **change in the active limb mask** (the fraction is averaged over live limbs,
so losing one shifts it against a baseline learned on a different set — measured +33 points).
Kept for the reasoning. Absent slots are hold-last
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

Before the CURRENT band's baseline locks, `m4 = null`, flagged `warming_up`.

🚩 **Rebuilt 2026-08-03 — a single baseline made `m4` an activity-change detector.** It locked
from **one tick's value** at the moment cumulative movement first hit 60 s — whatever the athlete
happened to be doing — and was never re-learned. But shock transmission differs between walking
and running as *physiology, not fatigue*, so changing pace moved `R/R_base` past the 50% full
scale and pinned `m4` at 100. Measured on a worn session: `m4` sat at **90–100 (saturated) while
jogging**, 75–85 running, and *unchanged during squats* — the activity its baseline had locked on.
Because `m4` fed `capacity` with a large weight (now `CAP_W_CTRL = 0.30`), that alone drove the
composite to 100 (§6.1).

Three fixes, all load-bearing:

1. **Per intensity band.** Baselines are learned separately for each band of
   `M4_BAND_EDGES`, so `m4` compares like with like and answers a question that is actually
   answerable: *"at the intensity being worked at right now, is shock being transmitted
   differently than when fresh at this same intensity?"*
2. **Time-weighted mean, not a snapshot.** One unlucky tick can no longer set the reference.
3. **Settle gate — `M4_SETTLE_S` = 3 transmission time constants = `3 × 20 s` = 60 s of
   movement, served *before* the band's own 60 s lock begins.** Nothing is learned until the
   20 s EMA has converged — it seeds from the first active tick, when the gravity baseline is
   still settling and `a_dyn` briefly carries most of 9.81 m/s², so the shank mean sweeps down
   through every band during warm-up. Without the gate a band locked on that sweep and read 100
   for the rest of the session. Measured: exactly that, on the activity ladder. **[V]**

The transmission EMA is also advanced **only while moving** — rest ticks used to fold in and drag
both limb means toward the noise floor, so a baseline locking shortly after a rest was measured
against a partly-rest-loaded EMA.

⚠️ The session snapshot is now **schema v4** (v2 replaced the scalar `R_base` with a per-band
table; v3 split `dose` into fast/slow pools; v4 added the per-band `r_mask` lock-time limb sets,
2026-08-06). A v1 or v2 snapshot is *rejected* rather than partly applied, since restoring a
single-band baseline would reintroduce the confound the bands exist to remove; a v3 snapshot is
accepted with its `m4` baselines dropped (§7.4).

**Result on the activity ladder:** `m4` now reads **4–14 across steady walking through sprinting**
and ~31 on irregular landing work, i.e. near its nominal 0 when movement is consistent — which is
what the metric always claimed to mean. **[V]**

⚠️ **`m4` is NOT validated against real data — see §11.1.** The squats log holds only ~20 s of
movement, an order below the **120 s** minimum (60 s settle + 60 s in-band lock), so **`m4` never
emits a value on the only capture that exists.** It ships on synthetic fixtures, validated live in S1-T15 step 4.

### 5.5 `m5` — L/R Balance

**Estimator: weighted Universal Symmetry Index (wUSI)**, not the classic Symmetry Index:
```
per tick (units m/s², same as σ):
    W_i  = max(0, 1 − 2σ² / (σ² + L_i² + R_i²))   noise-floor weight, σ from calibration (§3.8)
           ⚠️ the max(0,·) is load-bearing: un-clamped, W goes NEGATIVE below the noise floor and
           SUBTRACTS from the accumulators, able to drive them negative
if both_sides_active and moving:
    accL ← accL·2^(−Δt/ASYM_HALFLIFE) + W_i·L_i·Δt
    accR ← accR·2^(−Δt/ASYM_HALFLIFE) + W_i·R_i·Δt
else:
    FREEZE — no decay, no accumulation (see the warning below)
USI  = (accL − accR) / √(accL² + accR²)
m5   = copysign( 100 · clamp( |USI| / 0.18 , 0, 1 ), USI )   SIGNED: + = left, − = right
```
`ASYM_HALFLIFE = 5 min`.

🚩 **SELF-REBALANCE (2026-08-03).** The accumulators also relax toward each other every tick
both sides are streaming, with `ASYM_REBALANCE_HALFLIFE_S = 60 s` — whether or not the athlete
is moving. Without it m5 stayed pinned to whatever the last one-sided bout left behind: the
accumulators only updated while moving, so standing still froze the imbalance and only loading
the *other* leg could correct it.

⚠️ **Proportional decay cannot do this.** `USI = (L−R)/√(L²+R²)` is **scale-invariant** — that is
one of the five axioms it was chosen for (Alves 2020) — so multiplying both accumulators by the
same factor leaves it *exactly* unchanged. The imbalance has to be pulled toward the common mean:

```
mid = (accL + accR)/2 ;  acc ← mid + (acc − mid)·2^(−Δt/60 s)
```

which decays the **difference** while preserving the total.
`test_m5_returns_toward_even_on_its_own` fails against proportional decay, which is why it
exists. A 5 s hop is forgotten within a couple of minutes; a sustained limp keeps re-creating
the imbalance faster than it is shed, so a real asymmetry still holds.

🚩 The rebalance requires **both sides streaming**, exactly as accumulation does. A one-sided
sensor failure freezes the whole thing — without that the live side keeps growing while the dead
one decays and m5 hits maximum within seconds. `test_a_dead_side_still_cannot_move_m5` pins it.

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
persists beyond **`STALE_TIMEOUT_S` = 20 s**, emit `null` flagged `partial` rather than a stale
frozen value. *(The same 20 s constant governs m4's stale hold. An earlier draft said one
`ASYM_HALFLIFE`, i.e. 5 min — that was never what the code did and would hold a dead-sensor value
15× too long.)*

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

🚩 **`m5` is SIGNED as of 2026-08-03 (user decision): positive = LEFT-dominant, negative = RIGHT.**
The magnitude is `|m5|`, and everything that consumes it as a *severity* — the capacity term, the
insight rules — takes `abs()`. It is a **within-session** readout of which side is carrying more
right now, never a claim that a limb is weaker or at risk. The evidence below still stands and is
why the sign must not be presented as a finding or compared across sessions.

*The previous rule, superseded:* **Do not report direction.** Agreement on *which* limb is dominant is poor across sessions
(Cohen's κ = −0.14 to 0.60). Report magnitude only; `raw` retains the sign for later study. **[L]**

---

## 6. The composite — injury risk

### 6.1 Structure

```
demand_inst = 0.60·max(m1, m2) + 0.40·min(m1, m2)        soft-max, 0..100
if saturated: demand_inst += (100−demand_inst)·ROT_ESCALATION·rot/100   (§3.7.1)
demand      = EMA(demand_inst, DEMAND_TAU_S = 2 s)       fast; only de-noises
degradation = (0.50·m3 + 0.30·m4 + 0.20·|m5|) / Σ(available weights)
capacity    = 100 − 55·degradation/100                   45..100
ratio       = demand / capacity                          load vs capacity
composite   = min(100, 200 · ratio^n / (ratio^n + 1))    n = ACUTE_EXPONENT = 3.5
                                                         100 when demand == capacity

There is NO floor term. That is the point (§6.1a).
```

⚠️ **`acute` was rescaled 2026-08-02 after a live wearing session.** It was
`100·demand/(demand + capacity)` — a hyperbola whose value at `demand = 100` and a healthy
`capacity = 100` is exactly **50**. For an uninjured athlete the acute term therefore could not
exceed half scale however hard the session, and the upper half of a 0–100 "injury risk" was
reachable only through accumulated dose. Measured live over 11 minutes: `demand` p99 = 91 (max
98) yet composite p99 = 66, and the wearer independently reported the number "plateauing around
50" without having seen the formula.

The replacement keeps the load-versus-capacity ratio and the saturating shape, but raises the
ratio to a power (sigmoid rather than hyperbolic, so light activity stays low) and normalises so
that **`demand == capacity` reads 100** for any exponent. Degradation lowers `capacity`, so a
degraded athlete reaches full scale at lower demand — which is the intent.

🚩 **HISTORY (2026-08-03, superseded later the same day): `ACUTE_EXPONENT` raised 2 → 4.**
**The shipped value is `ACUTE_EXPONENT = 3.5`** — §6.1a re-fitted 4 → 3.5 against the worn
protocol once the dose floor was removed, and the §6.1 code block above is authoritative. Read
everything from here to the end of §6.1 as the *pre-§6.1a* state: the bold `n = 4` column of the
table below is no longer the live exponent, and the ladder it produces
(slow walk 0.9 / light jog 13.8 / hard run 57.4 / sprint 86.5) is **not** what the shipped model
reads. **The live ladder is §6.1a's** — squats 1.3, walk 2.6, jog 6.0, kick 8.4,
single-leg landing 17, jump 21 fresh (~33 at the fatigued capacity). The reasoning below is kept
because it is why the exponent is high at all.

At `n = 2` the curve was still far too eager
at the bottom: a slow walk read 20–30 and a light jog 50–70 on a scale labelled *injury risk*.
Measured on the §4.1 ladder at `n = 2`: slow walk 26.0, light jog 55.3 — reproducing the reported
complaint almost exactly. `n = 4` is not a fitted constant. Injury is a **threshold event, not an
average**: damage accumulates until it exceeds a critical threshold, or until load exceeds a
*declining* tissue strength, and mechanical damage is driven far more by load **magnitude** than
by load frequency (Kalkhoven — first-principles athletic-injury model). The bone
daily-stress-stimulus law uses `m = 4` (Whalen 1988) and fatigue-damage exponents span 2.1–5.8
(Pattin 1996), so a 4th-power ratio is the same physics `DOSE_EXPONENT` already applies. **[L]**

What the exponent does to the ladder, at fixed demand — the `n = 4` column is the **historical**
choice of 2026-08-03, not the shipped `3.5`:

| demand | n=2 | ~~n=4~~ *(historical)* | n=6 |
|---|---|---|---|
| 24 (slow walk) | 11.0 | 0.7 | 0.0 |
| 47 (light jog) | 36.4 | 9.4 | 2.2 |
| 71 (hard run) | 66.7 | 40.1 | 22.3 |
| 84 (sprint) | 83.1 | 67.2 | 52.9 |

~~Together with the §5.3 dose-law fix this is what moves the ladder to slow walk **0.9**, light jog
**13.8**, hard run **57.4**, sprint **86.5** (§4.1).~~ **Pre-§6.1a history, 2026-08-03** — that
ladder was computed at `n = 4` with the dose floor still present, and is superseded by §6.1a's
live ladder (squats 1.3, walk 2.6, jog 6.0, kick 8.4, single-leg landing 17, jump 21). Note the
two fixes of that day were independent: the exponent alone took a slow walk from 26.0 to 15.9,
and the remaining 15 points were the dose floor — which §6.1a then removed outright.

### 6.1a The dose floor was removed — measured, 2026-08-03 **[V]**

A 13-minute worn protocol (still, squats, walk, jog, jumps, single-leg landings, kicks, a limp,
squats to failure, rest) produced a composite spanning **15.5 to 18.5 across all of it**.
Standing still and squatting to failure were indistinguishable.

| Block | m1 max | m2 max | m3 | m4 | m5 | **composite** |
|---|---|---|---|---|---|---|
| still | 0 | 75 | 31.8 | 29 | 37 | **15.8–16.0** |
| squats | 13 | 31 | 31.1 | 16 | 38 | **15.6** |
| walk | 29 | 100 | 31.1 | 24 | 37 | **15.5–15.7** |
| jog | 32 | 74 | 33.1 | 32 | 32 | **15.6–18.2** |
| jumps | 49 | 58 | 34.7 | *null* | 24 | **17.1–18.2** |
| single-leg landing | 41 | 86 | 36.1 | 13 | 23 | **17.9–18.2** |
| kicks | 35 | 86 | 36.5 | 41 | 28 | **18.1–18.5** |
| limp | 28 | 40 | 36.9 | 40 | 30 | **18.4–18.5** |
| squats to failure | 21 | 49 | 36.9 | 6 | 28 | **18.4–18.5** |
| rest (85 s) | 0 | 0 | 36.9 | 4 | 27 | **18.4–18.5** |

Worked through on the jump block: `demand` ~36 smeared to ~20 by a 25 s EMA, `capacity` 94.6,
`ratio` 0.21 to the 4th power = 0.002, so `acute` contributed **0.33 points** and the other 16
were `floor = 0.50·m3`. Four dampers had stacked — additive floor, 25 s EMA, 4th-power curve,
capacity factor 0.15 — each defensible alone, never re-measured in combination. The synthetic
ladder ran each activity for 200 s, which hides both the smearing and transients entirely.

**Dose now reduces capacity instead of adding a floor**, which is what the load-vs-capacity model
actually says: fatigue does not add risk at rest, it makes the *same movement* riskier.

- still → demand 0 → **risk 0**, whatever the accumulated dose
- transient → visible within a tick; stop moving → decays immediately
- fatigued athlete → same movement, lower capacity, higher risk
- and **every primitive contributes**: m1/m2 through demand, m3/m4/m5 through capacity

`ACUTE_EXPONENT` 4 → **3.5**, fitted to the protocol's measured m1/m2 against the wearer's
targets for a *fresh* athlete: squats 1.3, walk 2.6, jog 6.0, kick 8.4, single-leg landing 17,
jump 21. The same jump at the capacity measured after the fatigue block reads ~33. Still inside
the fatigue-damage exponent range 2.1–5.8 (Pattin 1996).

🚩 **Capacity is bounded so unvalidated metrics cannot set the headline.** The live constant is
**`CAPACITY_SPAN = 55.0`** (biomech.py): `capacity = 100 − 55·degradation/100`, so it **floors at
45**. *(The older `CAPACITY_FACTOR` 0.70 → 0.15 narrative below is history — that constant is
superseded and survives in the code only because the forecast mirrors `FLOOR_FACTOR`. Ignore its
"floors at 85" figure; 45 is the current bound.)*
`m4`/`m5` carry the `unvalidated` flag (§11.1 — synthetic fixtures only), yet at 0.70 they could
remove **42 capacity points**, and since `acute` raises `demand/capacity` to a high power that
is far more leveraged than the linear form suggests. Measured at the time, at `demand` 60 and `m3` 0
(with the then-current exponent of 4):

| `m4` | `m5` | capacity | ratio | **composite** |
|---|---|---|---|---|
| 0 | 0 | 100.0 | 0.600 | **23** |
| 75 | 14 | 64.6 | 0.929 | **85** — crosses WARN |
| 90 | 0 | 62.2 | 0.965 | **93** — crosses ALERT |
| 90 | `null` | 37.0 | 1.622 | **100** — pinned |

The same physical session read 23 or 93 depending only on an unvalidated number, and the label
said `unvalidated` on the metric's own card while the value silently set the alert-severity
headline — which §2 forbids. The last row is the sharpest: `degradation` renormalises over
*available* terms (§8, so fewer sensors are not scored as lower risk), so whenever `m5` is `null`
— common, it freezes on any missing side — `m4` **alone** sets degradation at full value and its
weight (now **0.30**, `CAP_W_CTRL`) is cosmetic. Renormalisation is kept; the bound is what makes
it safe. **Widen `CAPACITY_SPAN` when `m4`/`m5` gain real-data validation (open item 10).** **[V]**

🚩 **`demand` is EXPOSURE, not peak.** `m1`/`m2` are 1 s peak-holds — correct for metrics named
Impact and Loading Rate — but feeding them straight into a risk index made **one landing outrank a
minute of running**. The composite now consumes `EMA(demand, DEMAND_TAU_S = 25 s)`; `m1`/`m2`
themselves are unchanged. Kalkhoven's model is about damage accumulating toward a threshold, so
the risk term should follow exposure.

🚩 **`DEMAND_TAU_S` is 2 s, not 25 s** (biomech.py). The 25 s EMA was one of the four stacked
dampers §6.1a removed: combined with the additive floor and the 4th-power curve it smeared a
whole jump block down to ~20 demand, which is how standing still and squatting to failure ended
up indistinguishable. At 2 s the EMA only de-noises — a transient is visible within a tick and
decays as soon as movement stops, which is the behaviour the composite is specified to have.

🚩 **SUPERSEDED 2026-08-03 — `m3` is back in `degradation`, and there is no `floor` term.**
The double-count this paragraph used to describe was resolved the other way: with the dose floor
removed (§6.1a), `m3` carries the accumulated-fatigue signal through `degradation` at the
**largest weight (0.50)**, which is what makes the same movement read higher on a depleted
athlete. `degradation` is therefore load *and* movement-quality degradation, not movement quality
alone — see the §6.1 code block, which is authoritative.

⚠️ **Consequence during warm-up:** `m4` and `m5` are both `null` until their gates clear —
**`m5` at 30 s of accumulated asymmetry time, `m4` at 120 s of movement** (60 s `M4_SETTLE_S`
then 60 s `M4_BASELINE_LOCK_S` in one band; corrected 2026-08-06, this paragraph used to say
60 s for both). `m3` is
**never** `null` (the dose accumulator always yields a float), so `degradation` always has at
least one available term and renormalises over it. Warm-up degradation is therefore `= m3`, and
for the second minute `= m3` and `|m5|`. The
composite's meaning is still narrower in the opening minutes, and it must be flagged
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
(the failure mode of a pure `max`). `capacity` floors at **45** (`CAPACITY_SPAN = 55`) so the
composite stays bounded.

🚩 **There is no `floor` term** (removed 2026-08-03, §6.1a). Accumulated dose no longer adds a
baseline the composite decays onto — it **reduces capacity** instead, so an athlete standing
still reads **0 whatever their dose**, and the same movement reads higher when they are depleted.
That is what the load-vs-capacity model actually says: fatigue does not add risk at rest, it
makes the same work riskier.

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
| Squatting, 16–31 s | 0.04 | 1.79 | 0.0 | `null` | `null` | **0.00** |

*(unchanged by the §6.1a rebuild: with `m3` = 0 there was never a floor on this capture, so removing the floor moved nothing here. The capture still validates only that a low-load activity reads low — see open item 13.)*
| Standing still, 34–41 s (after work) | 0.0 | 0.0 | 0.0 | `null` | `null` | **0.0** |

⚠️ **Re-measured 2026-08-03** after the §4 `m1`/`m2` re-anchoring, the §5.3 dose law and the §6.1
acute curve were corrected. Every value in this table is now ~0, and that is the honest reading
rather than a regression: this capture is **16 s of gentle bodyweight squatting** whose peak
dynamic acceleration is ~3.6 m/s² — below the new impact floor, and genuinely below *walking*
(~11 m/s²), which is why §6.4 already noted that "a squat has less heel strike than a step".

🚩 **This capture can therefore no longer validate any metric numerically.** It validates only
that a low-load activity reads low. The numeric validation has moved to the synthetic fixtures
and to `test_rest_reads_zero_however_much_dose_has_accumulated`. **Closing this needs a real
capture containing running and jumping — see open item 13 and
`scripts/calibrate_capture.py`.**

**`m3` is now 0 across the whole capture, and that is the point rather than a regression.** This
file holds **16 s of gentle squatting**, whose measured cube-mean load is ~14% of hard running;
cubed and integrated that is **0.0011 dose-minutes** against the current floor of 0.03 — still
~1.5 orders below (it was three orders below the 0.5 floor this paragraph was first written against).
§5.3 already argued this is the physically correct answer ("16 seconds of moderate squatting
genuinely is a negligible cumulative load"); the old scale said 13.7/100 only because its floor
was 0.6 s of hard-training equivalent, so any movement cleared it within a second.

**Consequence: this capture can no longer validate `m3` or the dose-decay behaviour** —
both are zero here, so the assertions would be vacuous.
**`test_rest_reads_zero_however_much_dose_has_accumulated`** takes that over on 5 minutes of
hard-run-equivalent synthetic load followed by 20 s of rest, asserting that dose accumulates,
persists through the rest, that `degradation > 0`, and that the **composite falls to ~0 at rest**
however much dose has been built. *(Renamed and rewritten 2026-08-03 with §6.1a — it replaces
`test_sustained_load_builds_dose_and_decays_to_the_floor`, which no longer exists. The old
"settles onto exactly `0.50 × m3`" clause went with it: **there is no dose floor any more.**)*

⚠️ **Re-measured 2026-08-02** after the §4 normalisation floors were raised against a live
wearing session. The earlier row read `m1` 44.1, `m2` 52.6, composite 35.2 for the same file:
gentle squatting scored mid-scale because `m1`'s floor sat barely above the sensor noise. **`m3`
is identical (7.0 / 13.7) across both measurements** — it does not depend on those floors, so it
is the control showing the retune moved only what it was meant to. Controlled squats now reading
below walking on `m1` is correct, not a regression: `m1` is *impact*, and a squat has less heel
strike than a step.

⚠️ **`m4` and `m5` are `null` in every row** — the log contains only 19.9 s of movement, below
their **120 s** / 30 s warm-ups. This table therefore validates `m1`, `m2`, `m3` and the composite
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
1 s of raw derived samples (~640/limb), each tick reduces its ~10 new samples to **two ringed
summaries** (`p90 adyn` → `pk_a`, `p90 jerk` → `pk_j`), and only **60 of those summaries**
(= 1 s) are retained. `m1`/`m2` then take the **`max` over those 60 values**, not a percentile.

*(Corrected 2026-08-06 against the shipped code: the rings are **float64**, not float32, there
are **two** of them rather than three, and each is paired with a **boolean validity mask**
(`ring_valid`) so a tick a limb produced no data for is excluded from the `max`. **`mean adyn`
is never ringed** — it is folded into the 20 s control EMA in-tick, which is O(1) and needs no
history.)*

🚩 **The two levels use different statistics and the pairing is mandatory — see §5.1.** The `p90`
is *within* the tick, where it rejects single-sample artifacts; the aggregate *across* the ring is
`max`, because a percentile across the ring makes an isolated impact invisible (a 50 m/s² impact
moves ring-p90 by 0.000). *(An earlier revision of this paragraph said "a percentile over 60
values", contradicting §5.1. §5.1 is correct.)*

| Buffer | Length | Contents | Bytes/device |
|---|---|---|---|
| Per-tick summaries | 60 ticks = 1.0 s × 4 limbs | 2 × float64 rings (`pk_a`, `pk_j`) + a bool `ring_valid` mask | **≈4,080 B** |
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
- Devices beyond `MAX_DEVICES` (`.env`, default 5) are never silently mixed into another
  device's stream. **Refined 2026-08-06 (shipped behaviour, `state.py`):** a new device at the
  cap first **displaces the longest-silent *offline* device** (one whose `last_seen` is older
  than `OFFLINE_AFTER_S`) and takes its place — a trainer swapping a wearable would otherwise
  wait out the whole session gap before the replacement could register. **Only when every
  tracked device is live** is the newcomer dropped and counted in
  `ingest:stats/global:dev_dropped` (logged once per device id). A separate `evict_stale()`
  sweep releases devices silent beyond `SESSION_GAP_S`.

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
| `m3` Accumulated Load | O(1), 15/90 min half-lives | ~0.0011 Hz (fast pool) | minutes |
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
| `r_base[]`, `r_sum[]`, `r_time[]` | float x M4_BANDS | `m4` PER-BAND baselines and their lock progress (§5.4). Introduced at **schema v2** — a v1 snapshot carrying the old scalar `R_base` is rejected outright, since restoring a single-band baseline would reintroduce the activity-change confound the bands exist to remove |
| `session_start_t`, `last_tick_t` | float (unix s) | the literal JSON keys; drive the `SESSION_GAP_S` decision on restore |
| `cal[limb_name]` | 5 × float | per sensor: `k`, `gyro_bias[3]`, `sigma` (§3.8) |
| `cal_src[limb_name]` | str | `default` / `carried` / `measured` — calibration provenance (§3.8) |
| `dose_fast`, `dose_slow` | float | the TWO dose pools (§5.3). **These are what `restore()` actually rebuilds from**, each decayed at its own half-life; the `dose` field above is an informational total |
| `move_t`, `asym_t` | float | movement-time accumulators — `move_t` gates m4's settle, `asym_t` is m5's 30 s warm-up gate |
| `r_mask[]` | list | **v4 (2026-08-06):** each band's lock-time limb set, serialised as **limb names** (never slot indices, same argument as `cal`) |
| `schema_version` (`v`) | int | **currently 4**; v3 is still accepted with its `m4` baselines dropped (see below); v1/v2 are rejected — v2 predates the fast/slow dose split, so its single `dose` scalar cannot be restored into two pools |

✅ **Fixed 2026-08-06 — `r_mask[]` now rides the snapshot (schema v4), so `m4`'s band baseline
survives a restore.** `m4` freezes whenever the active limb set differs from the mask its baseline
locked on (§5.4), which makes the mask part of the baseline. Before the fix `restore()` rebuilt
`r_base` but left `r_mask` empty, so every restored band took the freeze branch forever — `m4`
emitted nothing, with no `warming_up` flag, for the rest of the session. Now the mask is
serialised by limb name and restored with the baseline; a **v3** snapshot (no masks) is still
accepted but its `r_base` entries are dropped so the bands re-learn instead of freezing — a
baseline is never restored without its mask. Only the (un-snapshotted) intensity EMA re-warms
after a restart, so `m4` resumes within ~30–60 s. Pinned by
`test_m4_baseline_survives_snapshot_restore` and
`test_v3_snapshot_restores_dose_but_drops_m4_baselines`.

🚩 **Calibration MUST be keyed on the limb name (equivalently the sensor triple it is mapped
from), never on the slot index.** Slots are the **sorted `LIMB_MAP` limb names** (§7.2) — the
ticker emits every mapped limb on every tick, so a slot's identity comes from the sort order of
the configured names rather than from arrival order. Change `LIMB_MAP` and the indices move; so
if devices reconnect
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
  `now − last_tick_t` before use (`dose` and `accL`/`accR` have half-lives, so a restart during a
  long gap must not resurrect stale load — the two dose pools decay at their own half-lives).
  If `now − last_tick_t > SESSION_GAP_S`, discard and
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
| 2 — both shanks | ✔ | ✔ | ✔ | ✔² | ✔ | over `m4`,`m5`,`m3` |
| 2 — both thighs | ✔¹ | ✔ | ✔ | ✔² | ✔ | over `m4`,`m5`,`m3` |
| 1 — any | ✔¹ | ✔ | ✔ | ✔² | `null` | over `m4`,`m3` |
| 0 / no data | held | held | held | held | held | held |

¹ `m1` falls back to **all mapped limbs** (`impact_i` = every slot when no shank is mapped),
flagged `no_shank` — thigh impact is not the validated shank
surrogate. `quality` (TRD §4 step 8) already reflects missing sensors, so the UI can distinguish
"low risk" from "less data".

² **Corrected 2026-08-06.** These three rows previously read `null` for `m4`, which was true of
the shank/thigh **transmission ratio**. Since the 2026-08-03 tremor rebuild (§5.4) `m4` needs
only **one streaming limb** — the tremor fraction is measured per limb and averaged over whatever
is live — so it is available on every row above, and the composite's `degradation` renormalises
over `m4` too. `test_degradation_ladder` asserts exactly this (`both shanks` → m4 available,
single sensor → m4 available).

---

## 9. Display (`frontend/src/lib/metrics.ts`)

| ID | Display name | Short | Tooltip |
|---|---|---|---|
| `m1` | Impact | IMP | Peak shock magnitude reaching the lower leg |
| `m2` | Loading Rate | RATE | How abruptly load is being applied |
| `m3` | Accumulated Load | LOAD | Total mechanical work absorbed this session |
| `m4` | Movement Control | CTRL | Movement tremor vs. this athlete when fresh — rises as control degrades |
| `m5` | L/R Balance | BAL | How evenly load is shared left/right in this session — magnitude, with the side carrying more |
| `composite` | **Injury Risk** | RISK | Load applied vs. current capacity |

*(Tooltips synced to the shipped `metrics.ts` 2026-08-06. `m4`'s was rewritten on 2026-08-03 with
the tremor rebuild — it no longer says "shock absorption". `m5`'s now names the sign convention,
since 2026-08-03 made it signed. The composite's **"— a monitoring aid, not a prediction"**
qualifier was **removed on 2026-08-05 as part of the demo posture**; §2's claims limits are
unchanged and still bind any copy written for a real deployment.)*

Bands (display only, §6.3): **<10 low, 10–25 moderate, 25–45 elevated, ≥45 high**
(`RISK_BAND_CUTOFFS` in `metrics.ts`). **Re-cut 2026-08-03 for the §6.1a composite** — under the
old 0–30/30–60/60–80/80–100 cut the entire worn protocol, fatigue included, collapsed into
"low", because the §6.1a ladder puts a jump at ~21 fresh.

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
   published to `biomech:diag:{device_id}`, never written to `metrics`. **`raw`'s shipped key set
   (synced 2026-08-06):** `R` (the **unsigned tremor fraction** since the 2026-08-03 rebuild, no
   longer a signed shank/thigh ratio), `R_base`, `m4_band`, `m4_band_t`, **signed** `usi_pct`,
   **signed** `usi_fast_pct`, `dose`, `dose_fast`, `dose_slow`, `move_t`, `intensity`, `a_int`,
   `w_int`, `sat_frac`, `m1_lo`, `demand`, `degradation`, `cal_left`, and the **per-tick noise
   weight `W`** — `W` is there
   specifically because a constant `1.0` is the visible signature of the §5.5 bug (weighting
   applied to the accumulators instead of per tick). Unavailable values are published as `NaN`,
   never omitted. Flag vocabulary:

   | Flag | Meaning |
   |---|---|
   | `warming_up` | `m4`/`m5` still inside their **120 s / 30 s** warm-up (§5.4, §5.5). Only when the sensors those metrics need have actually streamed — see `degraded_sensors` |
   | `partial` (debounced ≥0.75 s: a lossy link flickers `active` tick-to-tick and an
     undebounced flag toggled hundreds of times per second in a live session) | a required sensor went inactive mid-session; `m4`/`m5` frozen or `null` |
   | `no_shank` | `m1` fell back to all mapped limbs (§5.1) |
   | `saturated` | >2.6% clipped samples; **`m1`/`m2` are LOWER BOUNDS**, render as ">= x" (§3.7). They are still reported -- suppression to `null` was removed 2026-08-03 |
   | `degraded_sensors` | device streaming <4 sensors (§8), **or** a sensor a metric requires was mapped but has never produced data (flat battery, bad strap). Both are "this value is never coming"; `warming_up` promises the opposite, so the two must not be confused. Tested against `ema_seen`, never against `LIMB_MAP` alone |
   | `uncalibrated` | at least one sensor running on default `k`/`bias`/`σ` — no history and no still window yet (§3.8) |
   | **`carried_over`** | **at least one sensor running last-known-good values from a PREVIOUS session (`biomech:cal:{dev}`), applied but not measured on this athlete today (§3.8)** |
   | `cal_failed` | a calibration attempt hit a validity guard and was rejected; last-known-good stands and detection continues |
   | **`unvalidated`** | **`m4`/`m5` — synthetic fixtures only, no real-data validation (§11.1). Set whenever `m4` or `m5` actually emits a value; a tick where both are `null` carries no `unvalidated` flag, because there is no unvalidated number on screen to qualify.** |

   ✅ **Fixed 2026-08-06 — the stale `pair_live` gate is gone.** The gate that chose between
   `warming_up` and `degraded_sensors` for a null `m4` used to require a **shank *and* a thigh**
   to have streamed — a leftover of the transmission-ratio era, wrong since the 2026-08-03 tremor
   rebuild made one streaming limb sufficient (§5.4, §8). It now reads `warming_up` whenever ANY
   mapped limb has ever streamed (`ema_seen.any()`), and `degraded_sensors` only when nothing has.
   Note the flags are **per-device unions**: a both-shanks rig now correctly shows `warming_up`
   (m4 is coming) *alongside* `degraded_sensors` (m5's both-sides gate genuinely is never
   satisfied with dead thighs) — both are true. Pinned by
   `test_partial_rig_m4_warms_up_and_arrives`.

   ⚠️ **Note 2026-08-05 (demo posture) — `unvalidated` is emitted but NOT displayed.** The flag
   is still set on the wire exactly as specified above, and `/api/health` still surfaces it, but
   the product UI hides it (`HIDDEN_FLAGS` in `frontend/src/lib/metrics.ts`, and the
   unvalidated chip is not rendered in `InsightsPanel.tsx`). **§11.1's requirement that the flag
   be *visible* to the trainer is therefore deliberately not met in the demo build** — the value
   is still qualified everywhere except on screen. Restore the rendering before anything is
   presented to a real trainer.

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
   read/written by ingest only (BACKEND_SCHEMA §4) — **no API route** — with a TTL of
   **30 days** (`CAL_CARRY_TTL_S`).

---

## 11. Validation plan (S1-T15 tests)

**Golden values — squats replay**, all **measured** this session, with generous tolerances: **[V]**

🚩 **This table is SUPERSEDED and no longer reproducible.** It was measured before the
2026-08-03 re-anchoring of `M1_LO_FLOOR`/`M1_HI`/`M2_LO`/`M2_HI`/`M3_LO` (§4) and before the dose
floor was removed (§6.1a). §6.4 re-measured the same capture afterwards and found **every value
in it now reads ~0** — which is the physically correct answer for 16 s of gentle squatting, not a
regression. Kept for provenance; the live assertions are the §11.1 fixtures and the test suite.

| Check | Expected (as measured pre-2026-08-03) |
|---|---|
| Still, 2–11 s | `m1` < 2, `m2` < 2, `m3` = 0, composite < 2 |
| Squats, 16–31 s | `m1` 9–18 (13.5), `m2` 13–23 (17.7), `m3` rising monotonically to ~7 (7.0), composite 7–15 (10.6) |
| Still, 34–41 s | `m1` < 4, `m3` ≈ 13.7 (holds), composite settled onto the then-present `0.50 × m3` dose floor — **there is no floor term today, so this now reads 0** |
| Transmission `R` during squats | 1.380 left, 1.382 right — **unrunnable today**: `raw.R` is now the unsigned tremor fraction, not a shank/thigh transmission ratio (§5.4) |
| `m4`, `m5` | **`null` in every row** — asserted explicitly, see §11.1 |

### 11.1 ⚠️ `m4` and `m5` ship WITHOUT real-data validation — deliberate, user-approved

`squats.bin` is the only capture that exists and it contains **19.9 s of movement**, below `m4`'s
**120 s** minimum (60 s `M4_SETTLE_S` then 60 s `M4_BASELINE_LOCK_S` in one band) and `m5`'s 30 s
warm-up. **Neither metric emits a single value on it.** Two of
five primitives therefore have **no golden values from real hardware**, and the composite is
validated only in the regime where its `degradation` term reduces to `m3`.

**Decision (user, this session): ship them on synthetic fixtures and validate live in S1-T15
step 4.** This must be visible in the code, not just here:

- `biomech.py` carries a module-level comment naming `m4` and `m5` as synthetically-validated
  only, pointing at this section.
- Their `Metrics` entries carry the flag **`unvalidated`** for the whole of stage 1, so the
  `/debug` viewer and `/api/health` can surface it.
  ⚠️ **Note 2026-08-05 (demo posture): the flag is emitted on the wire but the product UI hides
  it** (`HIDDEN_FLAGS` in `frontend/src/lib/metrics.ts`; the chip is not rendered in
  `InsightsPanel.tsx`). **This section's visibility requirement is therefore deliberately not met
  in the demo build** — see the matching note in §10.
- `test_biomech.py` **has no classes at all** — the suite is **46 module-level test functions**,
  grouped only by section-header comments. The synthetic-only caveat lives in the **module
  docstring**, not in a `TestSyntheticOnly` class. *(Corrected 2026-08-06; earlier revisions
  specified a class that was never written.)*

⚠️ **`m5`'s warm-up gate now counts accumulated-asymmetry time, not total movement time**
(fixed 2026-08-02). `move_t` advances on every moving tick including ones where a side was
inactive, so on a lossy link it passed the 30 s threshold while `accL`/`accR` were still nearly
empty — and `USI = (accL−accR)/√(accL²+accR²)` is unstable when both are tiny. Live result:
`m5`'s first emitted values read ~82 out of pure noise, then fell to ~15 once the accumulators
filled. The gate is now `asym_t`, incremented only when the accumulators are actually updated.

⚠️ **Open item — `ASYM_HALFLIFE_S = 5 min` is too slow to see a discrete event.** In one session,
90 s of deliberate one-sided loading (an exaggerated limp) moved `m5` only to ~27, diluted by the
preceding 2 minutes of symmetric squats. A later worn session confirmed the sharper form: a
**single-leg landing barely moved it at all**. Quantified: with a 5-minute half-life the
accumulator's time constant is 433 s, so one second of one-sided load displaces roughly **0.2%**
of it.

⚠️ **`m5`'s sensitivity is also NON-STATIONARY, and the metric never declares it.** The
accumulators start empty, so the effective averaging length is `min(elapsed, 433 s)`. Just after
the 30 s warm-up gate a one-second event is worth ~13 points; seven minutes later the same event
is worth ~0.9. So `m5` is roughly **14× more event-sensitive at the start of a session** than
later in it.

**What ships:** the chronic 5-minute accumulator is unchanged and remains the reported `m5`. A
**fast channel** (`ASYM_FAST_HALFLIFE_S` = 10 s) now runs alongside it and is published as
`raw.usi_fast_pct` — diagnostic only, so the trade-off can be sized on real data before anything
is promoted. It is **signed**, same convention as `usi_pct` (+ = left); the "magnitude only"
display rule applies to what is *shown to a trainer*, not to this diagnostic channel.

**What `m5` should read, for reference:** near **0** when symmetric. Values of 14–18 correspond to
roughly 2.5–3.2% actual asymmetry, which is squarely inside documented natural asymmetry for
uninjured athletes (2.3–3.1% for peak GRF) — so a steady 14 during running is plausibly *correct*
rather than a fault. ⚠️ But note `m5` is an **inter-sensor ratio**: with `M5_FULL_SCALE_USI = 0.18`
one percentage point of USI is 5.56 `m5` points, so a residual **2% left/right gain mismatch reads
`m5` ≈ 11 on a perfectly symmetric athlete**. On a sensor still flagged `uncalibrated` or
`carried_over` that bias is unbounded.

**Synthetic fixtures required** (each a generated frame stream, not a recording):

| Fixture | Asserts | In the suite? |
|---|---|---|
| 5 min symmetric movement | `m5` emits after 30 s and converges to ≈ 0 | **No** |
| 5 min with a **known 12% L/R load imbalance** | `m5` converges to 12% wUSI ⇒ ≈ 67 (±5) | **No** |
| 5 min with the tremor fraction ramped **+25%** after the band baseline locks | `m4` locks `R_base` after 120 s of movement (60 s settle + 60 s in band), then rises to ≈ 50 (±5) | **No** |
| ~~5 min with `R` ramped **−25%**~~ — **deleted 2026-08-06** | ~~`m4` also rises to ≈ 50 — proves direction-agnostic~~ **Wrong since 2026-08-03: `m4` IS directional** (`drift = tremor/base − 1`, clamped at 0), so a −25% ramp reads **0**, not ~50. The replacement claim, if it is ever written: a −25% ramp must read exactly 0 and must never be scored as a loss of control | — |
| One-sided sensor death at t = 90 s | `m5` freezes, then `null` + `partial`; **never** rises toward 100 | Yes — `test_a_dead_side_still_cannot_move_m5`, `test_one_sided_sensor_loss_does_not_read_as_asymmetry` |
| Shank death with thigh alive at t = 90 s | `m4` freezes, then `null` + `partial`; **never** pins at 100 | Yes — `test_shank_loss_does_not_pin_m4` |

⚠️ **Note 2026-08-06 — only the sensor-death rows are implemented.** The four
**convergence-value** fixtures above (symmetric → 0, 12% → ≈67, and the two ±25% `m4` ramps) are
**not** in `test_biomech.py`; the suite pins `m4`/`m5` *availability* and *failure modes* but
never their numeric convergence. So the numbers in those rows are specifications, not passing
assertions — which is a second, independent reason `m4`/`m5` carry `unvalidated`.

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
   *(As shipped, 2026-08-06: `test_orientation_invariance` runs a synthetic stream, not the
   squats log, and asserts **`m1`/`m2`/`composite` to `abs=1e-4` and `m3` to `abs=1e-6`** —
   not bit-identity; the residual is float64 round-off from rotating the inputs. **`m4` and `m5`
   are not checked at all.**)*
4. **Sample-rate independence** — decimate 600→300→150 Hz; `m3` within 5%; `m1` degrades
   gracefully, never jumps.
   *(As shipped: `test_dose_is_sample_rate_independent` is parametrised over rates and asserts
   only **`0 < m3 < 100` at each rate** — there is **no cross-rate 5% bound**, and `m1` is not
   tested here at all.)*
5. **Packet-loss robustness** — drop 30% of samples at random; `m3` within 10%.
   *(As shipped: `test_packet_loss_does_not_collapse_dose` asserts only **`m3 > 0` and
   `m1 > 0`** — no 10% bound. Open item 11 is why: the measured damage is far larger than 10%,
   so the bound could not honestly be asserted.)*
6. **Gravity-only input** — constant 9.81 m/s² on any axis ⇒ all primitives zero.
7. **Free-fall** — must not produce a spurious `m1` peak (§3.3 fold-over).
   *(As shipped: `test_free_fall_does_not_produce_a_phantom_impact` asserts **`m1 < 40`**, which
   bounds the fold-over rather than excluding a peak outright.)*
8. **Saturation floor** — a synthetic clipped impact => `m1`/`m2` still reported, `saturated`
    flag set; and a single clipped sample must NOT raise the flag. Plus: at a clipped impact,
    rotation must move `demand`, while pure rotation with no impact must not.
9. **Degradation ladder** — every row of §8 gives the right `null` pattern, and the composite
   does not fall merely because sensors are missing.
   *(As shipped: `test_degradation_ladder` asserts the `m4`/`m5` availability pattern, that
   `m1`/`m2`/`m3` are non-`null`, that `degraded_sensors` is flagged below 4 limbs, and that
   **`composite > 0`** — it does **not** compare the degraded composite against the 4-sensor
   value, so "does not fall merely because sensors are missing" is argued, not pinned.)*
10. **Held ticks** — empty `frames` repeats previous `Metrics`, accumulates no dose.
11. **Session reset** — a >300 s gap zeroes `dose`/`R_base`; a 3 s gap does not.
    *(As shipped: `test_session_reset_clears_dose_but_keeps_calibration` exercises the reset by
    calling **`reset_session()` directly**, then asserts `dose`/`move_t` are zero, every
    `r_base` band is `None`, and calibration survives. The gap **threshold** is not exercised:
    the "a 3 s gap does not reset" half is currently **unpinned**.)*
12. **Calibration correctness** — synthetic sensors given known gain/bias errors ⇒ the running
    sums recover them within 0.1%, and `m5` bias caused by a deliberate 3% L/R gain mismatch
    drops to <0.5 points after correction. *(The 3% is applied as ±1.5% per side; since the
    2026-08-04 widening of the stillness guard from 2% to **6%** (§3.8) either split would now
    calibrate — the symmetric one keeps the fixture's `m5` math simple. This parenthetical was
    the last remaining description of the old 2% guard; corrected 2026-08-06.)*
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
20. **Slot exhaustion** — a 6th device with `MAX_DEVICES = 5` is dropped and counted **when all
    five are live**, never mixed into an existing slot; when one of the five is *offline* the
    6th **displaces the longest-silent** of them instead (§7.2). Both halves are pinned:
    `test_max_devices_cap_drops_extra_live_devices`, `test_new_device_displaces_an_offline_one`,
    plus `test_evict_stale_releases_slots`.
21. **State snapshot round-trip** — snapshot mid-session, restart, restore: `dose`/`accL`/`accR`
    resume with the correct elapsed decay applied. A restore with
    `now − last_tick_t > SESSION_GAP_S` must discard and start fresh (§7.4).
    *`test_snapshot_round_trip_applies_elapsed_decay` asserts the dose/pool half: each pool ages
    at its own half-life, the slow pool survives, and an over-long gap is refused. The
    "`R_base` survives" half is pinned since 2026-08-06 by
    `test_m4_baseline_survives_snapshot_restore` (see the `r_mask` entry in §7.4).*
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

28. **Whole-device dropout breaks the still window** — after several seconds of accumulated
    stillness, feed held ticks (no limb has samples) for longer than `CAL_MAX_GAP_S`; the window
    must clear. A held tick that returns before the still-window update lets a window survive a
    dropout of any length (§3.8).
29. **Sensor fault vs restless athlete** — a perfectly motionless sensor reading 10% off gravity
    must raise `cal_failed` after `CAL_FAULT_S` and keep defaults; the same magnitude error *with*
    rotation present must never raise it, because motion explains that one (§3.8).
30. **Dead-from-the-start sensors** — a sensor mapped in `LIMB_MAP` that never streams must give
    `degraded_sensors`, never `warming_up` (which promises a value that is never coming), and
    `m5` must not go `null` with no flag at all. Tested against `ema_seen`; a healthy rig inside
    its warm-up must still say `warming_up` (§8, §10).

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
| 2 | 🚩 **Accelerometer range.** ±16 g clips on the **shank during running/jumping**; literature recommends ≥±32 g. Measured: dynamic accel tops out near 147 m/s², so 35 g / 42 g / 60 g / 100 g landings are indistinguishable on `m1` | **hardware is fixed for now (user)** | **Mitigated, not solved.** `m1`/`m2` report marked lower bounds (§3.7) and rotation discriminates above the ceiling (§3.7.1). Anything above 27.7 g resultant remains unmeasurable |
| 3 | Reference bounds (§4), composite weights (§6), `DOSE_EXPONENT`, and the borrowed **2.6% saturation threshold** (§3.7) — all provisional | post-MVP | Calibrate against trial data |
| 13 | **`A_DOSE_REF` / `W_DOSE_REF` and the §4 bounds are anchored to a SYNTHETIC gait generator, not to this device.** They set the whole scale of `m3` and where every activity lands on `m1`/`m2`. The only real capture (`squats.bin`) is 16 s of gentle squatting and now reads ~0 throughout (§6.4), so it cannot anchor them | **before trusting any absolute value** | **Open — tooling ready.** `scripts/calibrate_capture.py` replays a real worn capture through the shipped `compute()` and prints the cube-mean statistics each constant needs. Needs one session with walking, running and jumping, plus rough per-movement timestamps |
| 11 | **Packet loss degrades `m1` far harder than expected, and pushes `m2` the *wrong way*.** Measured 2026-08-03 by replaying `squats.bin` through `compute()` with per-sample random drop (squat phase, vs the 0% arm): 5% loss → `m1` −49%, `m2` **+34%**; 20% → `m1` −65%, `m2` **+42%**; 50% → `m1` −80%, `m3` −99%; 70% → all metrics collapse (composite −97%). `m1` falls because it is `p90` *within* a tick and a thinned tick under-estimates the peak; `m2` **rises** because the rate-adaptive `α = 1 − exp(−Δt/τ)` grows with the widened sample spacing, so the low-pass smooths less and more jerk survives. Per-sample uniform drop is the harshest model — real UDP loss is bursty, so these are an upper bound on the damage | before trusting any absolute metric on a lossy link | **Open.** Aggregates must not be compared across quality regimes |
| 12 | **`quality` ≈ 0.35 is probably not 65% packet loss.** The live 11-minute session read `quality` 0.35 yet produced coherent, well-ordered metrics (walking 29, jumps 56, intervals 77). Item 11 shows that a *genuine* 65–70% per-sample loss drives the composite to ~0.4 — three orders of magnitude away from what was observed. Since `quality` is a ratio against the **configured** `EXPECTED_INPUT_HZ` (changed 600 → 640 on 2026-08-02), a low reading is equally consistent with real loss and with that constant being too high. **A 0%-loss simulator control arm settles it in ten minutes** and should be run before any hardware work is done on the strength of the 0.35 figure | before acting on the loss figure | **Open — cheap to close** |
| 10 | **`m4`/`m5` have no real-data validation** — the only capture has 19.9 s of movement vs their **120 s** (60 s settle + 60 s in-band lock) / 30 s warm-ups. Shipping on synthetic fixtures by user decision; flagged `unvalidated` in code and surfaced in `/api/health` | **S1-T15 step 4** | §11.1 — needs one ≥10-min session with a deliberate fatigue block to close |
| 4 | `m4` direction — is transmission drift up or down under fatigue for *this* population? | post-MVP | **Moot since the 2026-08-03 tremor rebuild:** `m4` is **directional** (`drift = tremor/base − 1`, clamped at 0) and the literature *does* fix the sign of tremor. `raw.R` is now the unsigned tremor fraction, not a signed transmission ratio (§5.4) |
| 5 | UI claims language (§2) — "Injury Risk" as a product name vs. the evidence on composite validity | stage 3 frontend session | Flagged for your decision |
| 6 | Confirm no device ships a different full-scale range | before multi-device trials | ±16 g / ±2000 °/s assumed (user: **keeping ±16 g**) |
| 7 | ~~Session-state persistence~~ | — | **CLOSED — user approved.** Specified as required in §7.4 (1 Hz Redis snapshot, ~200 B/device, decay applied on restore) |
| 8 | ~~Calibration UX — who triggers it, and how the athlete is told to stand still~~ | — | **CLOSED — user decision, supersedes the deferral.** Nobody triggers it: automatic still-detection, every session, with last-known-good carried across sessions (§3.8). The UI's only job is to render `uncalibrated` / `carried_over` / `cal_failed` and mark the step change |
| 9 | Slot-table sizing: `MAX_DEVICES` (5) is fixed at startup (§7.2). Raising it needs an ingest restart — acceptable for MVP | post-MVP | At the cap a new device **displaces the longest-silent offline device**; it is dropped and counted only when all tracked devices are live (refined 2026-08-06, §7.2) |

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
