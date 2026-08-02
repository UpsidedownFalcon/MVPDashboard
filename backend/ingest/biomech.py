"""Biomech model — 5 orientation-free primitives + load-vs-capacity composite.

Implements docs/biomech/SPEC.md. Section references below are to that document;
read it before changing anything here, because most of the non-obvious choices
exist to avoid a specific, measured failure.

Orientation-free by mandate (SPEC Section 1): every quantity is a
rotation-invariant magnitude (|a|, |w|) or a derivative. No absolute angles, no
complementary/Kalman filter, no bone-aligned axis assumption.

    m1  Impact           peak dynamic accel at the shank
    m2  Loading Rate     exact linear jerk  ||da/dt + w x a||
    m3  Accumulated Load power-law-weighted decaying dose
    m4  Movement Control |drift| of shank->thigh transmission vs session baseline
    m5  L/R Balance      weighted Universal Symmetry Index of accumulated load
    composite            load vs capacity

All six outputs are 0..100. m1..m5 may be None (warm-up, missing sensors,
saturation); composite never is.

!! m4 AND m5 ARE VALIDATED ON SYNTHETIC FIXTURES ONLY (SPEC Section 11.1) !!
The only real capture (example/squats.bin) holds ~19.9 s of movement, below
m4's 60 s baseline lock and m5's 30 s warm-up, so neither metric emits a single
value on it. Both carry the `unvalidated` flag for the whole of stage 1. Do not
present them to a trainer as a finding until a >=10 min session with a
deliberate fatigue block has been recorded and SPEC Section 6.4 gains real rows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import lfilter

from common.scaling import (
    ACCEL_MS2_PER_COUNT,
    DEFAULT_CALIBRATION,
    GRAVITY_MS2,
    GYRO_DPS_PER_COUNT,
    SAT_SUPPRESS_FRACTION,
    SAT_THRESHOLD_COUNTS,
    Calibration,
)

# =============================================================================
# Tunables. All provisional starting points requiring calibration against trial
# data (SPEC Section 4) -- NOT validated clinical cut-offs. These are model
# parameters and deliberately live here, not in .env: if they drifted per
# deployment the numbers would stop being comparable between sessions.
# =============================================================================

# --- signal conditioning (SPEC Sections 3.5, 3.6) ---
FILTER_CUTOFF_HZ = 75.0      # not 50: 50 Hz keeps 97% of peak accel but only
                             # 36-75% of peak JERK, and m2 is a derivative
GRAVITY_TAU_S = 0.35         # baseline tracks the ~constant |g|
NOMINAL_INPUT_HZ = 600.0     # fallback dt only; real dt comes from `times`
MAX_DT_S = 0.05              # a gap larger than this is a discontinuity, not a
                             # sample interval -> fall back to nominal

# --- windows ---
PEAK_WINDOW_TICKS = 60       # 1.0 s of per-tick summaries at 60 Hz
CONTROL_TAU_S = 20.0         # m4 transmission-ratio averaging
MOVE_GATE_MS2 = 0.10         # tick-mean adyn: measured still 0.025-0.032,
                             # squatting 0.65-0.94 -- ~3x above the noise floor

# --- reference ranges (SPEC Section 4) ---
M1_LO_FLOOR = 0.15           # m1 lo = max(M1_LO_FLOOR, M1_LO_SIGMAS * sigma)
M1_LO_SIGMAS = 5.0           # noise-adaptive: a noisier sensor must not read >0 at rest
M1_HI = 150.0                # ~15 g, near accel full scale
M2_LO, M2_HI = 120.0, 12_000.0     # lo at the measured rest jerk floor (p99 48-57)
M3_LO, M3_HI = 0.01, 60.0          # dose-minutes; 16 s squats -> 14, 1 h hard -> 88
W_LO, W_HI = 5.0, 1500.0           # deg/s; still mean 1.6, squat mean 82, sprint >1200

# --- m3 dose (SPEC Section 5.3) ---
DOSE_EXPONENT = 3.0          # load accumulates as a POWER LAW, not linearly.
                             # Bone daily-stress-stimulus uses m=4 (Whalen 1988);
                             # fatigue damage ~2.1 sub-threshold (Pattin 1996);
                             # 3 is a deliberate conservative middle.
DOSE_HALFLIFE_S = 45 * 60.0

# --- m4 control (SPEC Section 5.4) ---
M4_BASELINE_LOCK_S = 60.0    # movement time before R_base locks
M4_FULL_SCALE = 0.50         # |R/R_base - 1| of 0.5 reads 100

# --- m5 balance (SPEC Section 5.5) ---
ASYM_HALFLIFE_S = 5 * 60.0
M5_WARMUP_S = 30.0           # the accumulator is rep-dominated before this
M5_FULL_SCALE_USI = 0.18     # from Delgado-Garcia 2025 (bilateral tibial
                             # accelerometry): asymmetry 9% -> 25% classic SI
                             # over a fatiguing run; USI ~ SI/sqrt(2) near
                             # symmetry -> fresh ~6.4%, fatigued ~17.7%

# How long a frozen m4/m5 stays held before going None + `partial`.
STALE_TIMEOUT_S = 20.0

# --- composite (SPEC Section 6.1) ---
DEMAND_MAX_W, DEMAND_MIN_W = 0.60, 0.40
DEGRADE_W_M4, DEGRADE_W_M5 = 0.45, 0.30    # m3 is NOT here: it drives `floor`
CAPACITY_FACTOR = 0.70                     # capacity floors at 30
FLOOR_FACTOR = 0.50

SESSION_GAP_S_DEFAULT = 300.0

# Limb-role parsing. LIMB_MAP names are configurable, so roles are derived from
# the name rather than hardcoded; this also makes fewer-sensor configs work.
_SHANK_WORDS = ("shin", "shank", "tibia", "calf")
_THIGH_WORDS = ("thigh", "femur", "quad")


def limb_role(limb: str) -> tuple[str | None, str | None]:
    """('left'|'right'|None, 'shank'|'thigh'|None) from a limb name."""
    low = limb.lower()
    side = "left" if "left" in low else "right" if "right" in low else None
    if any(w in low for w in _SHANK_WORDS):
        seg = "shank"
    elif any(w in low for w in _THIGH_WORDS):
        seg = "thigh"
    else:
        seg = None
    return side, seg


@dataclass
class Metrics:
    m1: float | None
    m2: float | None
    m3: float | None
    m4: float | None
    m5: float | None
    composite: float
    flags: frozenset[str] = frozenset()
    raw: dict = field(default_factory=dict)

    def as_list(self) -> list[float | None]:
        return [self.m1, self.m2, self.m3, self.m4, self.m5]


HELD_ZERO = Metrics(0.0, 0.0, 0.0, None, None, 0.0, frozenset({"warming_up"}), {})


def log_score(x: float, lo: float, hi: float) -> float:
    """0..100 on a log scale (SPEC Section 4).

    Log, not linear: the measured dynamic range between lifting and running is
    ~50x, so a linear scale calibrated for running renders every gym session
    as ~2/100.
    """
    if not math.isfinite(x) or x <= 0.0:
        return 0.0
    frac = math.log(max(x, 1e-12) / lo) / math.log(hi / lo)
    return 100.0 * min(max(frac, 0.0), 1.0)


def _onepole(alpha: float) -> tuple[list[float], list[float]]:
    """b, a for y[n] = alpha*x[n] + (1-alpha)*y[n-1]."""
    return [alpha], [1.0, -(1.0 - alpha)]


def _p90_axis0(x: np.ndarray) -> np.ndarray:
    """90th percentile down axis 0, linear interpolation. Same result as
    np.percentile(x, 90, axis=0), ~10x cheaper for the ~10-sample blocks we get:
    np.percentile's cost here is almost entirely dispatch/validation overhead,
    and it profiled as the single most expensive call in compute().
    """
    n = x.shape[0]
    if n == 1:
        return x[0].copy()
    pos = 0.9 * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    part = np.partition(x, (lo, hi), axis=0)
    a, b = part[lo], part[hi]
    return a + (pos - lo) * (b - a)


def _zi_for(alpha: float, steady: np.ndarray) -> np.ndarray:
    """Steady-state lfilter initial condition (transposed direct form II).

    For this one-pole it is (1-alpha)*v, NOT v. Getting it wrong injects
    exactly the transient it is meant to remove (SPEC Section 7.2.1).
    """
    return ((1.0 - alpha) * steady)[None, :]


class _Sess:
    """Per-device biomech state, stored in the caller's `state` dict under _KEY."""

    def __init__(self, limbs: tuple[str, ...]) -> None:
        self.limbs = limbs
        n = len(limbs)
        self.idx = {limb: i for i, limb in enumerate(limbs)}
        self.roles = [limb_role(limb) for limb in limbs]
        self.cal: dict[str, Calibration] = {limb: DEFAULT_CALIBRATION for limb in limbs}
        # Role index sets and the calibration-derived scalars are fixed for the
        # life of the session, so they are resolved once here rather than being
        # rebuilt 60 times a second.
        self.shank_i = np.array([i for i, (_, s) in enumerate(self.roles)
                                 if s == "shank"], dtype=int)
        self.thigh_i = np.array([i for i, (_, s) in enumerate(self.roles)
                                 if s == "thigh"], dtype=int)
        self.left_i = np.array([i for i, (s, _) in enumerate(self.roles)
                                if s == "left"], dtype=int)
        self.right_i = np.array([i for i, (s, _) in enumerate(self.roles)
                                 if s == "right"], dtype=int)
        self.impact_i = (self.shank_i if len(self.shank_i)
                         else np.arange(n, dtype=int))
        self.refresh_cal()
        # filter state
        self.z1: np.ndarray | None = None
        self.z2: np.ndarray | None = None
        self.zb: np.ndarray | None = None
        self.alpha_lp: float | None = None
        self.alpha_b: float | None = None
        self.last_a = np.zeros((n, 3))
        self.last_raw = np.zeros((n, 6))
        self.has_raw = np.zeros(n, dtype=bool)
        self.last_t = np.full(n, np.nan)
        self.seen_a = False
        # 1 s summary rings (SPEC Section 7)
        self.pk_a = np.zeros((n, PEAK_WINDOW_TICKS))
        self.pk_j = np.zeros((n, PEAK_WINDOW_TICKS))
        self.ring_valid = np.zeros((n, PEAK_WINDOW_TICKS), dtype=bool)
        self.ptr = 0
        # long-horizon EMAs (O(1) equivalents of SPEC 5.4's trailing means)
        self.ema_adyn = np.zeros(n)
        self.ema_seen = np.zeros(n, dtype=bool)
        # session accumulators
        self.dose = 0.0
        self.accL = 0.0
        self.accR = 0.0
        self.move_t = 0.0
        self.R_base: float | None = None
        self.m4_hold: float | None = None
        self.m5_hold: float | None = None
        self.m4_stale = 0.0
        self.m5_stale = 0.0
        self.prev: Metrics | None = None
        self.last_tick_t: float | None = None
        self.session_start_t: float | None = None

    def refresh_cal(self) -> None:
        """Recache the per-tick-invariant calibration derivatives."""
        self.k_vec = np.array([self.cal[limb].k for limb in self.limbs])[None, :, None]
        self.bias_vec = np.array([self.cal[limb].gyro_bias
                                  for limb in self.limbs])[None, :, :]
        self.sigma_mean = float(np.mean([self.cal[limb].sigma for limb in self.limbs]))
        self.all_default = all(self.cal[limb] is DEFAULT_CALIBRATION
                               for limb in self.limbs)

    # --- SPEC Section 7.4 snapshot ------------------------------------------
    def snapshot(self) -> dict:
        return {
            "v": 1,
            "dose": self.dose,
            "accL": self.accL,
            "accR": self.accR,
            "move_t": self.move_t,
            "R_base": self.R_base,
            "last_tick_t": self.last_tick_t,
            "session_start_t": self.session_start_t,
            # Keyed by LIMB NAME, never by slot index: slots are assigned
            # dynamically, so slot-keyed calibration would silently apply the
            # wrong sensor's gain after devices reconnect in a different order.
            "cal": {limb: c.as_dict() for limb, c in self.cal.items()},
        }

    def restore(self, snap: dict, now: float, session_gap_s: float) -> bool:
        if snap.get("v") != 1:
            return False
        last = snap.get("last_tick_t")
        if last is None or now - last > session_gap_s:
            return False                       # too old: start a fresh session
        elapsed = max(now - last, 0.0)
        self.dose = snap["dose"] * 0.5 ** (elapsed / DOSE_HALFLIFE_S)
        decay = 0.5 ** (elapsed / ASYM_HALFLIFE_S)
        self.accL = snap["accL"] * decay
        self.accR = snap["accR"] * decay
        self.move_t = snap["move_t"]
        self.R_base = snap["R_base"]
        self.last_tick_t = last
        self.session_start_t = snap.get("session_start_t")
        for limb, d in (snap.get("cal") or {}).items():
            if limb in self.cal:
                self.cal[limb] = Calibration.from_dict(d)
        self.refresh_cal()
        return True

    def reset_session(self) -> None:
        """New session: drop accumulated load and learned baselines, keep cal."""
        self.dose = 0.0
        self.accL = self.accR = 0.0
        self.move_t = 0.0
        self.R_base = None
        self.m4_hold = self.m5_hold = None
        self.m4_stale = self.m5_stale = 0.0
        self.session_start_t = None


_KEY = "_biomech"


def get_session(state: dict, limbs: tuple[str, ...]) -> _Sess:
    sess = state.get(_KEY)
    if sess is None or sess.limbs != limbs:
        sess = state[_KEY] = _Sess(limbs)
    return sess


def reset_session(state: dict) -> None:
    sess = state.get(_KEY)
    if sess is not None:
        sess.reset_session()


def snapshot(state: dict) -> dict | None:
    sess = state.get(_KEY)
    return sess.snapshot() if sess is not None else None


def restore(state: dict, snap: dict, limbs: tuple[str, ...], now: float,
            session_gap_s: float = SESSION_GAP_S_DEFAULT) -> bool:
    return get_session(state, tuple(sorted(limbs))).restore(snap, now, session_gap_s)


def set_calibration(state: dict, cal: dict[str, Calibration]) -> None:
    sess = state.get(_KEY)
    if sess is None:
        return
    for limb, c in cal.items():
        if limb in sess.cal:
            sess.cal[limb] = c
    sess.refresh_cal()


# =============================================================================
# Calibration (SPEC Section 3.8) -- OPTIONAL; the defaults are a working system.
# =============================================================================

def calibrate(frames: dict[str, np.ndarray]) -> dict[str, Calibration] | None:
    """Per-sensor gain/bias/noise from a still-stand window.

    Worth doing because m4 and m5 are inter-sensor RATIOS, so gain mismatch
    biases them directly. Returns None if any sensor fails a validity guard --
    rejecting is strictly better than baking in a bad correction.
    """
    from common.scaling import (
        CAL_K_MAX,
        CAL_K_MIN,
        CAL_MAX_G_ERROR,
        CAL_MAX_ROTATION_DPS,
    )

    out: dict[str, Calibration] = {}
    for limb, f in frames.items():
        if f is None or len(f) < 2:
            return None
        a = np.asarray(f[:, 0:3], dtype=np.float64) * ACCEL_MS2_PER_COUNT
        w = np.asarray(f[:, 3:6], dtype=np.float64) * GYRO_DPS_PER_COUNT
        amag = np.linalg.norm(a, axis=1)
        mean_a = float(amag.mean())
        if mean_a <= 0.0:
            return None
        if abs(mean_a - GRAVITY_MS2) / GRAVITY_MS2 > CAL_MAX_G_ERROR:
            return None                                   # sensor was not still
        if float(np.linalg.norm(w, axis=1).mean()) > CAL_MAX_ROTATION_DPS:
            return None                                   # athlete moved
        k = GRAVITY_MS2 / mean_a
        if not (CAL_K_MIN <= k <= CAL_K_MAX):
            return None
        out[limb] = Calibration(
            k=k,
            gyro_bias=w.mean(axis=0),
            sigma=float(np.std(amag - mean_a)) or DEFAULT_CALIBRATION.sigma,
        )
    return out or None


# =============================================================================
# compute()
# =============================================================================

def compute(
    frames: dict[str, np.ndarray],
    state: dict,
    times: dict[str, np.ndarray] | None = None,
) -> Metrics:
    """frames: limb -> float32[n, 6] (ax..gz, raw counts) since the last tick.

    times:  limb -> float64[n] server-mapped sample times in seconds. Optional;
            without it dt falls back to 1/NOMINAL_INPUT_HZ. m2 is a derivative,
            so real timestamps matter (SPEC Section 3.5).
    state:  persists across calls per device; holds filter state, the 1 s
            summary rings and the session accumulators.

    Called at OUTPUT_HZ per device. Returns Metrics(m1..m5, composite), 0..100.
    """
    limbs = tuple(sorted(frames.keys()))
    if not limbs:
        return HELD_ZERO
    sess = get_session(state, limbs)
    n_limbs = len(limbs)

    counts = [0 if frames[limb] is None else len(frames[limb]) for limb in limbs]
    n_max = max(counts)
    if n_max == 0:
        # Held tick: repeat the previous values, accumulate no dose.
        return sess.prev if sess.prev is not None else HELD_ZERO

    # --- 1. assemble [n_max, n_limbs, 6]; HOLD-LAST fill for short/absent limbs.
    # Never zero-fill: it decays the gravity baseline, so a sensor reconnecting
    # while the athlete stands still emits a ~9.75 m/s^2 phantom impact and m1
    # reads ~85/100 out of nothing (SPEC Section 7.2.1).
    block = np.empty((n_max, n_limbs, 6), dtype=np.float64)
    valid = np.zeros((n_max, n_limbs), dtype=bool)
    active = np.zeros(n_limbs, dtype=bool)
    for i, limb in enumerate(limbs):
        n_i = counts[i]
        if n_i:
            f = np.asarray(frames[limb], dtype=np.float64)
            block[:n_i, i, :] = f
            valid[:n_i, i] = True
            active[i] = True
            sess.last_raw[i] = f[-1]
            sess.has_raw[i] = True
        if n_i < n_max:
            fill = sess.last_raw[i] if sess.has_raw[i] else np.zeros(6)
            block[n_i:, i, :] = fill

    # --- 2. scale + apply calibration
    a_raw = block[:, :, 0:3] * (ACCEL_MS2_PER_COUNT * sess.k_vec)
    w_dps = block[:, :, 3:6] * GYRO_DPS_PER_COUNT - sess.bias_vec

    sat_any = (np.abs(block) >= SAT_THRESHOLD_COUNTS).any(axis=2)
    n_valid = int(valid.sum())
    sat_frac = float((sat_any & valid).sum()) / max(n_valid, 1)

    # --- 3. dt per sample, from real timestamps where available.
    # One np.diff over a [n_max+1, n_limbs] time matrix rather than a diff plus
    # a median per limb: np.median was measured as the single most expensive
    # call in compute().
    nominal_dt = 1.0 / NOMINAL_INPUT_HZ
    tmat = np.empty((n_max + 1, n_limbs))
    for i, limb in enumerate(limbs):
        n_i = counts[i]
        t_i = None
        if n_i and times is not None and times.get(limb) is not None:
            cand = np.asarray(times[limb], dtype=np.float64)
            if len(cand) == n_i:
                t_i = cand
        if t_i is None:
            # no usable timestamps: synthesise a nominal ramp for this limb
            anchor = sess.last_t[i] if np.isfinite(sess.last_t[i]) else 0.0
            tmat[1:, i] = anchor + (np.arange(n_max) + 1) * nominal_dt
            tmat[0, i] = anchor
        else:
            tmat[1:n_i + 1, i] = t_i
            if n_i < n_max:            # hold-last padded rows advance nominally
                tmat[n_i + 1:, i] = t_i[-1] + (np.arange(n_max - n_i) + 1) * nominal_dt
            prev_t = sess.last_t[i]
            tmat[0, i] = prev_t if np.isfinite(prev_t) else t_i[0] - nominal_dt
            sess.last_t[i] = t_i[-1]
    dt_arr = np.diff(tmat, axis=0)
    bad = ~((dt_arr > 0.0) & (dt_arr < MAX_DT_S))
    if bad.any():
        dt_arr[bad] = nominal_dt

    # --- 4. filter (SPEC Section 3.6). Alphas come from MEASURED dt, so the
    # model stays rate-flexible as TRD Section 4 requires.
    #
    # dt is QUANTISED to 1 us before deriving the alphas. Without this, ordinary
    # timestamp jitter changes alpha on nearly every tick, which re-seeds the
    # filters every tick -- and a filter re-seeded from the current sample has
    # base == amag, so adyn collapses to 0 and every metric silently reads zero.
    dt_nom = max(float(dt_arr.mean()), 1e-6)
    dt_q = round(dt_nom, 6)
    alpha_lp = 1.0 - math.exp(-dt_q / (1.0 / (2.0 * math.pi * FILTER_CUTOFF_HZ)))
    alpha_b = 1.0 - math.exp(-dt_q / GRAVITY_TAU_S)
    b_lp, a_lp = _onepole(alpha_lp)
    b_b, a_b = _onepole(alpha_b)

    flat = np.ascontiguousarray(a_raw.reshape(n_max, n_limbs * 3))
    if sess.z1 is None or sess.alpha_lp != alpha_lp:
        # First tick, or the input rate moved enough to change the coefficients:
        # (re)seed from this block's first sample so no step transient is injected.
        seed = flat[0]
        sess.z1 = _zi_for(alpha_lp, seed)
        sess.z2 = _zi_for(alpha_lp, seed)
        sess.alpha_lp = alpha_lp
    y, sess.z1 = lfilter(b_lp, a_lp, flat, axis=0, zi=sess.z1)
    y, sess.z2 = lfilter(b_lp, a_lp, y, axis=0, zi=sess.z2)
    a_f = y.reshape(n_max, n_limbs, 3)

    amag = np.sqrt(np.einsum("ijk,ijk->ij", a_f, a_f))
    if sess.zb is None or sess.alpha_b != alpha_b:
        sess.zb = _zi_for(alpha_b, amag[0])
        sess.alpha_b = alpha_b
    base, sess.zb = lfilter(b_b, a_b, amag, axis=0, zi=sess.zb)
    adyn = np.abs(amag - base)

    # --- 5. exact gravity-free jerk (SPEC Section 3.4):
    #     ||df/dt + w x f|| == ||da_world/dt||, from measured signals only.
    # w MUST be in radians/s here. Feeding deg/s makes m2 wrong by 57.3x and it
    # looks plausible rather than obviously broken.
    prev_a = np.empty_like(a_f)
    prev_a[0] = sess.last_a if sess.seen_a else a_f[0]
    prev_a[1:] = a_f[:-1]
    w_rad = w_dps * (math.pi / 180.0)
    da = (a_f - prev_a) / dt_arr[:, :, None]
    # cross product written out: np.cross is generic and profiled ~3x the cost
    ax, ay, az = a_f[:, :, 0], a_f[:, :, 1], a_f[:, :, 2]
    wx, wy, wz = w_rad[:, :, 0], w_rad[:, :, 1], w_rad[:, :, 2]
    jx = da[:, :, 0] + (wy * az - wz * ay)
    jy = da[:, :, 1] + (wz * ax - wx * az)
    jz = da[:, :, 2] + (wx * ay - wy * ax)
    jerk = np.sqrt(jx * jx + jy * jy + jz * jz)
    sess.last_a = a_f[-1].copy()
    sess.seen_a = True

    # --- 6. per-tick summaries -> 1 s ring.
    # p90 WITHIN the tick rejects single-sample spikes; the peak-hold is a MAX
    # ACROSS the ring (step 8). A percentile across the ring is silently broken:
    # an isolated 50 m/s^2 impact moves it by 0.000 (SPEC Section 5.1).
    p = sess.ptr
    tick_mean_adyn = np.zeros(n_limbs)
    tick_mean_w = np.zeros(n_limbs)
    wmag = np.sqrt(w_dps[:, :, 0] ** 2 + w_dps[:, :, 1] ** 2 + w_dps[:, :, 2] ** 2)
    active_counts = {c for c in counts if c}
    if len(active_counts) == 1:
        # Fast path: every streaming limb released the same number of samples,
        # which is the overwhelmingly common case. One percentile call for all
        # limbs instead of two per limb -- np.percentile is ~15 us of overhead
        # each, so this is most of compute()'s cost.
        n_i = active_counts.pop()
        pk_a_all = _p90_axis0(adyn[:n_i])
        pk_j_all = _p90_axis0(jerk[:n_i])
        mean_a_all = adyn[:n_i].mean(axis=0)
        mean_w_all = wmag[:n_i].mean(axis=0)
        sess.pk_a[:, p] = np.where(active, pk_a_all, 0.0)
        sess.pk_j[:, p] = np.where(active, pk_j_all, 0.0)
        tick_mean_adyn = np.where(active, mean_a_all, 0.0)
        tick_mean_w = np.where(active, mean_w_all, 0.0)
        sess.ring_valid[:, p] = active
    else:
        for i in range(n_limbs):
            n_i = counts[i]
            if n_i:
                sess.pk_a[i, p] = float(_p90_axis0(adyn[:n_i, i:i + 1])[0])
                sess.pk_j[i, p] = float(_p90_axis0(jerk[:n_i, i:i + 1])[0])
                tick_mean_adyn[i] = float(adyn[:n_i, i].mean())
                tick_mean_w[i] = float(wmag[:n_i, i].mean())
                sess.ring_valid[i, p] = True
            else:
                sess.pk_a[i, p] = 0.0
                sess.pk_j[i, p] = 0.0
                sess.ring_valid[i, p] = False
    sess.ptr = (p + 1) % PEAK_WINDOW_TICKS

    # --- 7. elapsed time for the session accumulators
    now_t = None
    if times:
        ends = [float(np.asarray(times[limb])[-1])
                for i, limb in enumerate(limbs)
                if counts[i] and times.get(limb) is not None]
        if ends:
            now_t = max(ends)
    step = 1.0 / 60.0
    if now_t is not None and sess.last_tick_t is not None:
        step = min(max(now_t - sess.last_tick_t, 0.0), 1.0)
    if now_t is not None:
        sess.last_tick_t = now_t
        if sess.session_start_t is None:
            sess.session_start_t = now_t

    # m4's trailing transmission mean, as an O(1) EMA
    alpha_ctl = 1.0 - math.exp(-step / CONTROL_TAU_S)
    upd = active & sess.ema_seen
    new = active & ~sess.ema_seen
    sess.ema_adyn = np.where(
        upd, sess.ema_adyn + alpha_ctl * (tick_mean_adyn - sess.ema_adyn),
        np.where(new, tick_mean_adyn, sess.ema_adyn),
    )
    sess.ema_seen |= active

    flags: set[str] = set()
    if sat_frac > 0.0:
        flags.add("saturated")
    if sess.all_default:
        flags.add("uncalibrated")
    if n_limbs < 4:
        flags.add("degraded_sensors")

    # --- 8. primitives ------------------------------------------------------
    sigma = sess.sigma_mean          # cached; only changes on calibration
    m1_lo = max(M1_LO_FLOOR, M1_LO_SIGMAS * sigma)

    shank_i = sess.shank_i
    thigh_i = sess.thigh_i
    impact_i = sess.impact_i
    if len(shank_i) == 0:
        flags.add("no_shank")

    def ring_max(arr: np.ndarray, rows: np.ndarray) -> float:
        sel = arr[rows]
        vsel = sess.ring_valid[rows]
        if not vsel.any():
            return 0.0
        return float(np.where(vsel, sel, -np.inf).max())

    if sat_frac > SAT_SUPPRESS_FRACTION:
        m1 = m2 = None            # a truncated peak is worse than no peak
    else:
        m1 = log_score(ring_max(sess.pk_a, impact_i), m1_lo, M1_HI)
        m2 = log_score(ring_max(sess.pk_j, list(range(n_limbs))), M2_LO, M2_HI)

    # m3 dose: power law, decays always, accumulates only while moving.
    a_int = float(tick_mean_adyn[active].mean()) if active.any() else 0.0
    w_int = float(tick_mean_w[active].mean()) if active.any() else 0.0
    intensity = max(log_score(a_int, m1_lo, M1_HI), log_score(w_int, W_LO, W_HI))
    sess.dose *= 0.5 ** (step / DOSE_HALFLIFE_S)
    moving = a_int > MOVE_GATE_MS2
    if moving:
        sess.dose += (intensity / 100.0) ** DOSE_EXPONENT * (step / 60.0)
        sess.move_t += step
    m3 = log_score(sess.dose, M3_LO, M3_HI)

    # m4: shank->thigh transmission drift vs this athlete's own fresh baseline.
    # Direction-agnostic (|.|): the literature does not fix the sign -- shock
    # attenuation usually INCREASES under fatigue, and injured runners showed
    # GREATER lower-body attenuation (SPEC Section 5.4).
    m4 = None
    R_now = None
    legs_ok = (len(shank_i) > 0 and len(thigh_i) > 0
               and bool(active[shank_i].all()) and bool(active[thigh_i].all()))
    sh_mean = float(sess.ema_adyn[shank_i].mean()) if len(shank_i) else 0.0
    th_mean = float(sess.ema_adyn[thigh_i].mean()) if len(thigh_i) else 0.0
    if legs_ok and moving and sh_mean > MOVE_GATE_MS2:
        R_now = th_mean / sh_mean
        if sess.R_base is None and sess.move_t >= M4_BASELINE_LOCK_S:
            sess.R_base = R_now
        if sess.R_base:
            m4 = 100.0 * min(abs(R_now / sess.R_base - 1.0) / M4_FULL_SCALE, 1.0)
            sess.m4_hold = m4
            sess.m4_stale = 0.0
    if m4 is None:
        # Freeze rather than compute. If a shank drops out while its thigh
        # survives, R = thigh/shank explodes and m4 pins at 100 -- a hardware
        # fault rendering as the most alarming possible finding.
        sess.m4_stale += step
        if sess.m4_hold is not None and sess.m4_stale <= STALE_TIMEOUT_S:
            m4 = sess.m4_hold
        elif sess.m4_hold is not None and not legs_ok:
            # `partial` means SENSORS are missing. Simply standing still also
            # freezes m4, but that is normal and must not raise a fault flag.
            flags.add("partial")
    if sess.R_base is None:
        flags.add("warming_up")

    # m5: wUSI over decaying accumulators. Both the per-tick noise weighting
    # (where the units match) and the both-sides gate are load-bearing -- SPEC
    # Section 5.5 records the measured failure modes of getting either wrong.
    left_i, right_i = sess.left_i, sess.right_i
    sides_ok = (len(left_i) > 0 and len(right_i) > 0
                and bool(active[left_i].all()) and bool(active[right_i].all()))
    m5 = None
    usi_pct = None
    if sides_ok and moving:
        L = float(tick_mean_adyn[left_i].mean())
        R = float(tick_mean_adyn[right_i].mean())
        w_noise = 1.0 - 2.0 * sigma**2 / (sigma**2 + L * L + R * R)
        decay = 0.5 ** (step / ASYM_HALFLIFE_S)
        sess.accL = sess.accL * decay + w_noise * L * step
        sess.accR = sess.accR * decay + w_noise * R * step
    denom = math.sqrt(sess.accL**2 + sess.accR**2)
    if sides_ok and sess.move_t >= M5_WARMUP_S and denom > 0.0:
        usi = (sess.accL - sess.accR) / denom
        usi_pct = 100.0 * usi
        m5 = 100.0 * min(abs(usi) / M5_FULL_SCALE_USI, 1.0)
        sess.m5_hold = m5
        sess.m5_stale = 0.0
    else:
        if sess.move_t < M5_WARMUP_S:
            flags.add("warming_up")
        sess.m5_stale += step
        if sess.m5_hold is not None and sess.m5_stale <= STALE_TIMEOUT_S:
            m5 = sess.m5_hold
        elif sess.m5_hold is not None and not sides_ok:
            # As for m4: `partial` is a missing-sensor signal, not a rest signal.
            flags.add("partial")

    if m4 is not None or m5 is not None:
        flags.add("unvalidated")           # SPEC Section 11.1 -- synthetic only

    # --- 9. composite (SPEC Section 6.1) ------------------------------------
    if m1 is None and m2 is None:
        demand = 0.0
    elif m1 is None or m2 is None:
        demand = float(m1 if m2 is None else m2)
    else:
        demand = DEMAND_MAX_W * max(m1, m2) + DEMAND_MIN_W * min(m1, m2)
    terms = [(DEGRADE_W_M4, m4), (DEGRADE_W_M5, m5)]
    avail = [(w, v) for w, v in terms if v is not None]
    degradation = (sum(w * v for w, v in avail) / sum(w for w, _ in avail)
                   if avail else 0.0)
    capacity = 100.0 - CAPACITY_FACTOR * degradation
    acute = 100.0 * demand / (demand + capacity) if (demand + capacity) > 0 else 0.0
    floor = FLOOR_FACTOR * m3
    composite = floor + (100.0 - floor) * acute / 100.0

    metrics = Metrics(
        m1=m1, m2=m2, m3=m3, m4=m4, m5=m5,
        composite=float(min(max(composite, 0.0), 100.0)),
        flags=frozenset(flags),
        raw={
            "R": R_now if R_now is not None else float("nan"),
            "R_base": sess.R_base if sess.R_base is not None else float("nan"),
            "usi_pct": usi_pct if usi_pct is not None else float("nan"),
            "dose": sess.dose,
            "move_t": sess.move_t,
            "intensity": intensity,
            "a_int": a_int,
            "w_int": w_int,
            "sat_frac": sat_frac,
            "m1_lo": m1_lo,
            "demand": demand,
            "degradation": degradation,
        },
    )
    sess.prev = metrics
    return metrics
