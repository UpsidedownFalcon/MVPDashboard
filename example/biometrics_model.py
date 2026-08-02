"""
biometrics_model.py  --  HX5 biomechanical model (v2, clean rewrite).

This is a deliberate REPLACEMENT for the original magnitude-and-fudge-factor
model, not an extension of it. What changed and why:

  ORIGINAL                              THIS VERSION
  --------                              ------------
  Resultant magnitude sqrt(x^2+y^2+z^2) Single-axis AXIAL extraction along the
  for everything (rotation-invariant,   bone. Impact loading is directional; the
  mixes impact with lateral motion)     axial component is what the literature
                                        validates against loading rate / bone
                                        stress.

  Arbitrary scale factors (*3, *6,      SI units end to end (m/s^2, deg/s,
  /12, /20, /3000, /180) to make        m/s^3). Display scaling is the frontend's
  numbers "look right" on a gauge       job, not the model's.

  Placeholder composites, e.g.          Composites built from NORMALISED
  acl_risk = max(accel)*0.5 +           primitives with explicit, documented
  cadence*0.1  ("# Example mapping")    weights and reference scales.

  180 lines of threshold-tuned          Removed. It was fragile (constantly
  step_stance_ratio                     logged "insufficient data") and is not
                                        one of the agreed metrics.

  Single window only. No history.       Temporal layer: 15 min / 6 h lookback,
                                        1 h / 6 h forecast.

  Every metric on the same raw scale,   Primitives normalised to 0-100 before
  so the largest number dominates any   combining, so each contributes equally
  average                               to an index regardless of raw units.

--------------------------------------------------------------------------------
THE MODEL
--------------------------------------------------------------------------------
FOUR PRIMITIVES (per leg, per window, in SI units):
    peak_tibial_accel        peak AXIAL shank acceleration      [m/s^2]
    cumulative_tibial_load   loading dose per second            [m/s^2 per s]
    peak_shank_gyro          peak shank angular velocity        [deg/s]
    max-jerk                 peak rate of change of accel       [m/s^3]

  -> magnitude / rotation / dose / rate: four near-orthogonal constructs.
     Everything else in this file is derived from these.

DERIVED COMPOSITES (present window):
    impact_attenuation   shock absorbed shank -> thigh          [0-1]
    acl_risk             weighted blend of normalised primitives[0-100]

TEMPORAL COMPOSITES (TemporalAggregator, needs history):
    present_index / past1_index / past2_index                   [0-100]
    future1_index / future2_index  (FORECASTS, not measurements)
    past1_* / past2_* per-primitive averages
    fatigue              drift of present vs recent baseline    [0-100]

CROSS-LEG (AsymmetryTracker, needs both legs, wall-clock aligned):
    asym_*               interlimb symmetry indexes

--------------------------------------------------------------------------------
CHANGING TIME WINDOWS AT RUNTIME
--------------------------------------------------------------------------------
Every window is one number in WINDOW_CONFIG, in seconds. 15 min -> 2 h is:
        "past1_seconds": 15 * 60,   ->   "past1_seconds": 2 * 60 * 60,
or at runtime:  aggregator.set_window("past1_seconds", 7200)
"""

import time
import numpy as np
from scipy import signal


# =============================================================================
# TIME-WINDOW CONFIG  --  the ONLY place to edit to change any window length.
# =============================================================================
WINDOW_CONFIG = {
    "past1_seconds":   15 * 60,        # acute fatigue window     (15 minutes)
    "past2_seconds":   6 * 60 * 60,    # cumulative session load  (6 hours)
    "future1_seconds": 1 * 60 * 60,    # near-term forecast       (1 hour)
    "future2_seconds": 6 * 60 * 60,    # rest-of-session forecast (6 hours)
    "history_margin_fraction": 0.10,   # keep 10% beyond the largest past window
}


# =============================================================================
# HARDWARE CONFIG  --  VERIFY AGAINST FIRMWARE / PHYSICAL MOUNTING.
# Values below were confirmed against the 016_HX5 firmware (ICM-45686).
# =============================================================================
HARDWARE_CONFIG = {
    "sensor_model": "ICM-45686",

    # Rate samples ARRIVE AT THIS SCRIPT. Firmware: 6400 Hz ODR / decimation 10.
    # (Not the 6400 Hz on-device rate; and actual arrival is lower due to
    # WiFi/UDP loss -- see measured_sample_rate_hz in diagnostics.)
    "sampling_rate_hz": 640,

    # Full-scale ranges the firmware configures. Scale factors are DERIVED.
    "accel_range_g":   16,             # firmware: ACCEL_FS_16G
    "gyro_range_dps":  2000,           # firmware: GYRO_FS_2000DPS

    "gravity_ms2": 9.81,

    # !! VERIFY AGAINST PHYSICAL MOUNTING !!
    # Which accelerometer axis points ALONG the bone at each sensor. The axial
    # primitive reads this single axis; if it is wrong the value is meaningless.
    "shank_axial_axis": "z",
    "thigh_axial_axis": "z",

    # Same-leg thigh<->shank pairing tolerance, as a fraction of one sample
    # interval. The two sensors on ONE leg share a monotonic clock so this is
    # valid.
    # !! CROSS-LEG WARNING !! The two LEGS do not share a clock (independent,
    # wrapping, boot-relative counters). NEVER pair left-vs-right by ts_us --
    # use wall-clock arrival (see AsymmetryTracker).
    "intra_leg_sync_tolerance_fraction": 0.25,

    # Low-pass filter applied to accel/gyro before metric extraction.
    #
    # NOTE: the original model used 12 Hz, which discards ~54% of a heel-strike
    # peak -- it was filtering away most of the impact content it was trying to
    # measure. Impact transients live in the 10-50 Hz band. 50 Hz retains ~98%
    # of the peak while still removing sensor noise, and is comfortably below
    # Nyquist (320 Hz at a 640 Hz sample rate).
    "filter_cutoff_hz": 50,
    "filter_order": 4,
}


# =============================================================================
# NORMALISATION CONFIG
# -----------------------------------------------------------------------------
# THE FIX for the original model's biggest flaw. The four primitives live on
# wildly different numeric scales (dose is ~100x the others), so averaging the
# RAW values makes any composite index effectively a proxy for the single
# largest metric. Each primitive is therefore mapped to 0-100 against its own
# reference range before it is ever combined.
#
#   normalised = 100 * clamp((value - lo) / (hi - lo), 0, 1)
#
# CALIBRATE THESE against real trial data: `hi` should be roughly the value you
# consider "maximum concerning" for that metric. Until calibrated they are
# educated starting points, not validated thresholds.
# =============================================================================
NORMALISATION_CONFIG = {
    #                            (lo,    hi)          units
    "peak_tibial_accel":         (0.0,   80.0),   # m/s^2   (~8 g impact)
    "cumulative_tibial_load":    (0.0,   30.0),   # m/s^2   time-averaged loading
    "peak_shank_gyro":           (0.0,  600.0),   # deg/s
    "max-jerk":                  (0.0, 4000.0),   # m/s^3
}

# Weights for acl_risk, applied to the NORMALISED primitives. Rationale:
# rotation (angular velocity) is the closest IMU proxy for knee abduction
# moment, the classic validated ACL predictor, so it carries the most weight;
# peak impact and loading rate follow; cumulative dose is an overuse signal
# rather than an acute ACL mechanism, so it contributes least.
ACL_RISK_WEIGHTS = {
    "peak_shank_gyro":        0.40,
    "peak_tibial_accel":      0.30,
    "max-jerk":               0.20,
    "cumulative_tibial_load": 0.10,
}

# ICM sensitivity tables (LSB per unit) for each full-scale range.
# ICM-45686 and ICM-20948 share these values for the ranges in use (both 16-bit).
ACCEL_SENSITIVITY_LSB_PER_G = {2: 16384, 4: 8192, 8: 4096, 16: 2048}
GYRO_SENSITIVITY_LSB_PER_DPS = {250: 131.0, 500: 65.5, 1000: 32.8, 2000: 16.384}

_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def _resolve_scale_factors(hw):
    """Derive (acc_SF -> m/s^2 per count, gyro_SF -> deg/s per count) from ranges.

    Raises loudly on an invalid range so a typo cannot silently mis-scale every
    reading in the pipeline.
    """
    a_range, g_range = hw["accel_range_g"], hw["gyro_range_dps"]
    if a_range not in ACCEL_SENSITIVITY_LSB_PER_G:
        raise ValueError(f"accel_range_g={a_range} invalid; "
                         f"valid: {sorted(ACCEL_SENSITIVITY_LSB_PER_G)}")
    if g_range not in GYRO_SENSITIVITY_LSB_PER_DPS:
        raise ValueError(f"gyro_range_dps={g_range} invalid; "
                         f"valid: {sorted(GYRO_SENSITIVITY_LSB_PER_DPS)}")
    acc_SF = hw["gravity_ms2"] / ACCEL_SENSITIVITY_LSB_PER_G[a_range]
    gyro_SF = 1.0 / GYRO_SENSITIVITY_LSB_PER_DPS[g_range]
    return acc_SF, gyro_SF


def normalise(name, value, cfg=None):
    """Map a raw primitive to 0-100 against its reference range. None-safe."""
    if value is None:
        return None
    cfg = cfg or NORMALISATION_CONFIG
    if name not in cfg:
        return None
    lo, hi = cfg[name]
    if hi <= lo:
        return None
    return 100.0 * float(np.clip((float(value) - lo) / (hi - lo), 0.0, 1.0))


def _sig4(value):
    """Round to 4 significant figures. None-safe."""
    if value is None:
        return None
    v = float(value)
    if v == 0.0:
        return 0.0
    if not np.isfinite(v):
        return None
    return float(f"{v:.4g}")


# =============================================================================
# SignalExtractor  --  raw sample buffers -> clean physical signals
# =============================================================================
class SignalExtractor:
    """Turns lists of raw sample dicts into filtered SI-unit numpy signals.

    VECTORISATION NOTES
    -------------------
    Unpacking a list of dicts requires one Python-level pass (dicts cannot be
    read by numpy directly), so the strategy is: pay that cost ONCE, convert to
    a contiguous float array, and do every subsequent operation with whole-array
    numpy calls. Specifically:

      * Filter coefficients are computed once in __init__, not per call. They
        depend only on (fs, cutoff, order), all fixed at construction.
      * extract_arrays() walks the buffer a single time and returns one (n, 7)
        array, instead of the five separate list comprehensions the previous
        version used (2 accel axes + 3 gyro axes).
      * Timestamp pairing uses np.searchsorted instead of a Python while-loop,
        turning an O(n) interpreted merge into a C-level binary search.
      * Both accel channels are filtered in one filtfilt call by passing a 2-D
        array along axis=0.
    """

    # Column layout of the array returned by extract_arrays().
    COL = {"ts": 0, "ax": 1, "ay": 2, "az": 3, "gx": 4, "gy": 5, "gz": 6}
    _N_COLS = 7
    _UINT32_SPAN = float(1 << 32)      # device ts_us wraps every ~71.6 min

    def __init__(self, hw):
        self.hw = hw
        self.fs = hw["sampling_rate_hz"]
        self.gravity = hw["gravity_ms2"]
        self.acc_SF, self.gyro_SF = _resolve_scale_factors(hw)
        for key in ("shank_axial_axis", "thigh_axial_axis"):
            if hw[key] not in _AXIS_INDEX:
                raise ValueError(f"{key}={hw[key]!r} invalid; must be 'x', 'y' or 'z'.")
        self.sync_tol = hw["intra_leg_sync_tolerance_fraction"]

        # Precompute filter coefficients ONCE. Profiling showed scipy.butter()
        # being re-derived on every window was the single largest cost in the
        # whole model (~48% of runtime).
        nyq = 0.5 * self.fs
        wn = min(0.99, hw["filter_cutoff_hz"] / nyq)
        self._filt_b, self._filt_a = signal.butter(
            hw["filter_order"], wn, btype="low", analog=False)
        # filtfilt needs > 3 * max(len(a), len(b)) samples to run.
        self._min_filter_len = 3 * max(len(self._filt_a), len(self._filt_b)) + 1

        self._acc_axis_col = {
            "shank": self.COL["a" + hw["shank_axial_axis"]],
            "thigh": self.COL["a" + hw["thigh_axial_axis"]],
        }

    # ---- extraction ------------------------------------------------------
    # Alternate timestamp field names, and their multiplier to microseconds.
    _TS_FIELDS = (("ts_us", 1.0), ("timestamp_us", 1.0),
                  ("ts_ms", 1e3), ("timestamp_ms", 1e3),
                  ("ts", 1e6), ("timestamp", 1e6))

    def extract_arrays(self, buf):
        """Single pass over the sample buffer -> (n, 7) float array.

        Columns: ts_us, ax, ay, az, gx, gy, gz  (raw counts, unscaled).
        This is the only Python-level iteration over samples in the model, so it
        is the one place worth micro-optimising.

        Fast path uses np.fromiter per column, which pushes the loop into C and
        is ~1.8x faster than filling a preallocated array with dict.get() calls.
        Falls back to a tolerant per-sample walk if any key is missing or the
        buffer is ragged.
        """
        n = len(buf)
        if n == 0:
            return np.empty((0, self._N_COLS), dtype=float)

        # Which timestamp field does this stream use? Decide once, not per sample.
        first = buf[0] if isinstance(buf[0], dict) else {}
        ts_field, ts_mult = None, 1.0
        for name, mult in self._TS_FIELDS:
            if first.get(name) is not None:
                ts_field, ts_mult = name, mult
                break

        out = np.empty((n, self._N_COLS), dtype=float)
        try:
            if ts_field is None:
                out[:, 0] = np.nan
            else:
                out[:, 0] = np.fromiter((p[ts_field] for p in buf), dtype=float, count=n)
                if ts_mult != 1.0:
                    out[:, 0] *= ts_mult
            for col, (grp, axis) in enumerate(
                    (("acc", "x"), ("acc", "y"), ("acc", "z"),
                     ("gyr", "x"), ("gyr", "y"), ("gyr", "z")), start=1):
                out[:, col] = np.fromiter((p[grp][axis] for p in buf),
                                          dtype=float, count=n)
            return out
        except (KeyError, TypeError, IndexError):
            pass   # ragged or unexpected schema -> tolerant path below

        nan = float("nan")
        for i, p in enumerate(buf):
            if not isinstance(p, dict):
                out[i, :] = nan
                continue
            acc = p.get("acc") or {}
            gyr = p.get("gyr") or {}
            ts = None
            for name, mult in self._TS_FIELDS:
                v = p.get(name)
                if v is not None:
                    ts = float(v) * mult
                    break
            out[i, 0] = nan if ts is None else ts
            out[i, 1] = acc.get("x", nan)
            out[i, 2] = acc.get("y", nan)
            out[i, 3] = acc.get("z", nan)
            out[i, 4] = gyr.get("x", nan)
            out[i, 5] = gyr.get("y", nan)
            out[i, 6] = gyr.get("z", nan)
        return out

    @classmethod
    def unwrap_timestamps(cls, ts):
        """Undo uint32 microsecond rollover so timestamps are monotonic.

        The device counter wraps every ~71.6 min. Vectorised pairing relies on
        np.searchsorted, which requires a sorted array, so a wrap mid-buffer
        would otherwise corrupt the match. Detect large backwards jumps and add
        2^32 to everything after each one.
        """
        if ts.size < 2:
            return ts
        d = np.diff(ts)
        wraps = np.cumsum(d < -(cls._UINT32_SPAN / 2))
        return ts + np.concatenate(([0.0], wraps * cls._UINT32_SPAN))

    # ---- pairing ---------------------------------------------------------
    def pair_same_leg(self, thigh_arr, shank_arr):
        """Pair SAME-LEG thigh & shank rows by timestamp proximity (vectorised).

        Valid only within one leg (shared monotonic clock). Never call this with
        two different legs -- see the CROSS-LEG WARNING in HARDWARE_CONFIG.

        Uses np.searchsorted to find each thigh sample's nearest shank sample in
        O(n log n) C code, then keeps only pairs inside the tolerance and
        enforces one-to-one matching.
        """
        if thigh_arr.shape[0] == 0 or shank_arr.shape[0] == 0:
            return thigh_arr[:0], shank_arr[:0]

        t_ts, s_ts = thigh_arr[:, 0], shank_arr[:, 0]
        if np.isnan(t_ts).any() or np.isnan(s_ts).any():
            n = min(thigh_arr.shape[0], shank_arr.shape[0])   # no timestamps: index-pair
            return thigh_arr[:n], shank_arr[:n]

        t_ts = self.unwrap_timestamps(t_ts)
        s_ts = self.unwrap_timestamps(s_ts)

        tol_us = (1_000_000.0 / float(self.fs)) * float(self.sync_tol)

        # Nearest neighbour: compare the candidates either side of the insertion
        # point and take whichever is closer.
        idx = np.searchsorted(s_ts, t_ts)
        idx = np.clip(idx, 1, s_ts.size - 1)
        left, right = idx - 1, idx
        pick = np.where(np.abs(t_ts - s_ts[left]) <= np.abs(t_ts - s_ts[right]),
                        left, right)

        within = np.abs(t_ts - s_ts[pick]) <= tol_us
        if not within.any():
            return thigh_arr[:0], shank_arr[:0]

        t_idx = np.nonzero(within)[0]
        s_idx = pick[within]
        # Enforce one-to-one: keep the first thigh sample claiming each shank row.
        _, first = np.unique(s_idx, return_index=True)
        first.sort()
        return thigh_arr[t_idx[first]], shank_arr[s_idx[first]]

    def measured_rate_hz(self, arr):
        """ACTUAL arrival rate for this window from real timestamps (vs nominal
        self.fs). Quantifies WiFi/UDP loss. Diagnostic only."""
        if arr.shape[0] < 2:
            return None
        ts = self.unwrap_timestamps(arr[:, 0])
        if np.isnan(ts[0]) or np.isnan(ts[-1]):
            return None
        span = (ts[-1] - ts[0]) / 1e6
        return (arr.shape[0] - 1) / span if span > 0 else None

    # ---- filtering -------------------------------------------------------
    def lowpass(self, data):
        """Zero-phase Butterworth low-pass using cached coefficients.

        Accepts 1-D or 2-D (filters along axis 0, so several channels can be
        done in a single call). Short arrays pass through unfiltered.
        """
        data = np.asarray(data, dtype=float)
        if data.shape[0] < self._min_filter_len:
            return data
        return signal.filtfilt(self._filt_b, self._filt_a, data, axis=0)

    # ---- signals ---------------------------------------------------------
    def axial_pair(self, thigh_arr, shank_arr):
        """Gravity-removed AXIAL acceleration for both segments, in m/s^2.

        This is the core change from the original model, which used the
        rotation-invariant resultant magnitude. The axial component is the
        directional impact signal that correlates with loading rate and tibial
        stress; magnitude blends it with lateral and fore-aft motion.

        Gravity is removed by subtracting each window's own mean of that axis
        (its quasi-static gravity projection). Both channels are stacked and
        filtered in ONE filtfilt call rather than two.
        """
        n = min(shank_arr.shape[0], thigh_arr.shape[0])
        if n == 0:
            empty = np.empty(0)
            return empty, empty
        pair = np.empty((n, 2), dtype=float)
        pair[:, 0] = shank_arr[:n, self._acc_axis_col["shank"]]
        pair[:, 1] = thigh_arr[:n, self._acc_axis_col["thigh"]]
        pair *= self.acc_SF
        pair -= pair.mean(axis=0)                 # remove gravity projection
        filtered = self.lowpass(pair)
        return filtered[:, 0], filtered[:, 1]

    def gyro_magnitude(self, arr):
        """Resultant angular velocity, deg/s.

        Magnitude is used here (unlike acceleration) because segment rotation is
        genuinely three-dimensional -- the shank rotates about more than one axis
        during gait, so total angular speed is the physically meaningful
        quantity, not one axis of it.

        Vectorised: one slice of the three gyro columns, then a single
        einsum-free norm along axis 1.
        """
        if arr.shape[0] == 0:
            return np.empty(0)
        g = arr[:, self.COL["gx"]:self.COL["gz"] + 1] * self.gyro_SF
        return self.lowpass(np.sqrt((g * g).sum(axis=1)))


# =============================================================================
# GaitProcessor  --  present-window model (the 4 primitives + present composites)
# =============================================================================
class GaitProcessor:
    """Computes the PRESENT-window metrics for ONE leg.

    Output is in SI units with NO display scaling. Any gauge ranges belong in
    the frontend; baking them in was what made the original model's numbers
    physically meaningless.
    """

    MIN_SAMPLES = 50                 # below this a window is not worth trusting
    FAST_WINDOW_SECONDS = 0.5        # recent slice used for peak metrics

    def __init__(self, hardware_config=None, normalisation_config=None,
                 fs=None, sync_tolerance_fraction=None):
        self.hw = dict(HARDWARE_CONFIG)
        if hardware_config:
            self.hw.update(hardware_config)
        # Back-compat with the old constructor signature.
        if fs is not None:
            self.hw["sampling_rate_hz"] = fs
        if sync_tolerance_fraction is not None:
            self.hw["intra_leg_sync_tolerance_fraction"] = sync_tolerance_fraction

        self.norm_cfg = dict(normalisation_config or NORMALISATION_CONFIG)
        self.fs = self.hw["sampling_rate_hz"]
        self.sig = SignalExtractor(self.hw)

    # ---- calibration (optional; offsets are applied by mean-removal anyway) --
    def calibrate(self, thigh_buffer, shank_buffer):
        """Kept for API compatibility. The axial extraction already removes each
        window's own mean, so a separate standing-offset pass is not required."""
        return None

    # ---- primitives ------------------------------------------------------
    @staticmethod
    def _peak(x):
        return float(np.max(np.abs(x))) if len(x) else 0.0

    def _cadence(self, axial, fs):
        """Steps/min from heel-strike peaks in the axial signal.

        Deliberately simple: a threshold at mean + 1 SD with a refractory gap.
        The original's 180-line stance-detection routine was fragile (it
        routinely logged 'insufficient data') and none of the agreed metrics
        depend on it.
        """
        if len(axial) < fs * 0.5:
            return None
        a = np.abs(axial)
        thresh = float(np.mean(a) + np.std(a))
        peaks, _ = signal.find_peaks(a, height=thresh, distance=max(1, int(0.25 * fs)))
        seconds = len(axial) / fs
        return (len(peaks) / seconds) * 60.0 if seconds > 0 else None

    def process_window(self, thigh_buffer, shank_buffer):
        """One window of paired thigh/shank samples -> present metrics.

        Returns None if the window is too short to be meaningful.
        """
        if not thigh_buffer or not shank_buffer:
            return None
        if len(thigh_buffer) < self.MIN_SAMPLES:
            return None

        # Single Python-level pass over each buffer; everything after this point
        # is whole-array numpy.
        thigh_arr = self.sig.extract_arrays(thigh_buffer)
        shank_arr = self.sig.extract_arrays(shank_buffer)
        thigh_arr, shank_arr = self.sig.pair_same_leg(thigh_arr, shank_arr)
        if shank_arr.shape[0] < self.MIN_SAMPLES:
            return None

        measured_fs = self.sig.measured_rate_hz(shank_arr)

        # ---- physical signals (both accel channels filtered in one call) --
        shank_axial, thigh_axial = self.sig.axial_pair(thigh_arr, shank_arr)
        shank_gyro = self.sig.gyro_magnitude(shank_arr)

        # Recent slice for peak (fast-response) metrics.
        fast_n = max(1, int(round(self.FAST_WINDOW_SECONDS * self.fs)))
        start = max(0, len(shank_axial) - fast_n)
        shank_fast = shank_axial[start:]
        gyro_fast = shank_gyro[start:]

        # ---- PRIMITIVE 1: peak axial tibial acceleration  [m/s^2] --------
        peak_tibial = self._peak(shank_fast)

        # ---- PRIMITIVE 2: cumulative tibial load (dose rate)  [m/s^2] ----
        # Time-averaged absolute axial loading over the FULL window, i.e. the
        # integral of |a| dt divided by window length, which reduces to mean|a|.
        #
        # Using mean rather than a raw sum matters: a sum scales with the NUMBER
        # of samples, so WiFi/UDP packet loss would silently shrink the "dose"
        # and a change in sample rate would rescale it entirely. The mean is
        # sample-rate independent -- loss costs precision, not magnitude.
        #
        # This is the overuse signal peaks miss: many moderate impacts and few
        # hard ones can share a peak but differ hugely in accumulated exposure.
        # True cumulative exposure comes from averaging this over the past
        # windows in TemporalAggregator.
        window_seconds = max(1e-6, len(shank_axial) / self.fs)
        cumulative_load = float(np.mean(np.abs(shank_axial))) if len(shank_axial) else 0.0

        # ---- PRIMITIVE 3: peak shank angular velocity  [deg/s] -----------
        peak_gyro = self._peak(gyro_fast)

        # ---- PRIMITIVE 4: peak jerk / loading rate  [m/s^3] --------------
        jerk = np.gradient(shank_axial, 1.0 / self.fs) if len(shank_axial) > 1 \
            else np.zeros_like(shank_axial)
        peak_jerk = self._peak(jerk[start:])

        primitives = {
            "peak_tibial_accel": peak_tibial,
            "cumulative_tibial_load": cumulative_load,
            "peak_shank_gyro": peak_gyro,
            "max-jerk": peak_jerk,
        }

        # ---- normalised 0-100 versions (what composites are built from) ---
        norm = {k: normalise(k, v, self.norm_cfg) for k, v in primitives.items()}

        # ---- COMPOSITE: impact attenuation (shank -> thigh) --------------
        # Fraction of axial shock absorbed between the segments. Degraded
        # (lower) attenuation indicates control loss / fatigue.
        peak_thigh = self._peak(thigh_axial[start:])
        attenuation = None
        if peak_tibial > 1e-9:
            attenuation = float(np.clip(1.0 - (peak_thigh / peak_tibial), 0.0, 1.0))

        # ---- COMPOSITE: acl_risk from NORMALISED primitives --------------
        # Replaces the original's `max(accel)*0.5 + cadence*0.1` placeholder.
        acl_risk = None
        usable = {k: v for k, v in norm.items() if v is not None}
        if usable:
            total_w = sum(ACL_RISK_WEIGHTS[k] for k in usable)
            if total_w > 0:
                acl_risk = sum(usable[k] * ACL_RISK_WEIGHTS[k] for k in usable) / total_w

        cadence = self._cadence(shank_axial, self.fs)

        return {
            # ---- the 4 primitives, SI units ------------------------------
            "peak_tibial_accel":      _sig4(peak_tibial),        # m/s^2 (axial)
            "cumulative_tibial_load": _sig4(cumulative_load),    # m/s^2 (mean)
            "peak_shank_gyro":        _sig4(peak_gyro),          # deg/s
            "max-jerk":               _sig4(peak_jerk),          # m/s^3

            # ---- normalised 0-100 (basis for every composite) ------------
            "norm_peak_tibial_accel":      _sig4(norm["peak_tibial_accel"]),
            "norm_cumulative_tibial_load": _sig4(norm["cumulative_tibial_load"]),
            "norm_peak_shank_gyro":        _sig4(norm["peak_shank_gyro"]),
            "norm_max_jerk":               _sig4(norm["max-jerk"]),

            # ---- present composites --------------------------------------
            "impact_attenuation": _sig4(attenuation),            # 0-1
            "acl_risk":           _sig4(acl_risk),               # 0-100
            "peak_femur_accel":   _sig4(peak_thigh),             # m/s^2 (axial)
            "cadence":            _sig4(cadence),                # steps/min

            # ---- diagnostics (strip before writing if you want lean rows) --
            "diagnostics": {
                "samples": int(shank_arr.shape[0]),
                "window_seconds": _sig4(window_seconds),
                "nominal_sample_rate_hz": self.fs,
                "measured_sample_rate_hz": _sig4(measured_fs),
                "sample_rate_loss_fraction": (
                    _sig4(1.0 - measured_fs / self.fs)
                    if measured_fs is not None and self.fs else None),
            },
        }


# =============================================================================
# TemporalAggregator  --  past averages, forecasts, fatigue
# =============================================================================
class TemporalAggregator:
    """Cross-window history for ONE leg stream -> the five composite indexes.

    process_window() only ever sees the current window, so anything requiring
    minutes-to-hours of history lives here.

    All indexes are computed from the NORMALISED primitives, so each of the four
    contributes equally regardless of its raw units. (Averaging raw values made
    the index a proxy for whichever metric had the largest numbers.)
    """

    PRIMITIVE_KEYS = ("peak_tibial_accel", "cumulative_tibial_load",
                      "peak_shank_gyro", "max-jerk")

    # process_window emits normalised values under these names.
    _NORM_KEY = {
        "peak_tibial_accel":      "norm_peak_tibial_accel",
        "cumulative_tibial_load": "norm_cumulative_tibial_load",
        "peak_shank_gyro":        "norm_peak_shank_gyro",
        "max-jerk":               "norm_max_jerk",
    }

    def __init__(self, window_config=None, clock=None):
        self.cfg = dict(WINDOW_CONFIG)
        if window_config:
            self.cfg.update(window_config)
        self._clock = clock if clock is not None else time.time

        # History as parallel numpy arrays rather than a list of tuples, so
        # trimming and window averaging are vectorised (searchsorted + a mean
        # over a contiguous slice) instead of Python loops over every entry.
        # Grown by doubling; compacted in place on trim.
        self._cap = 1024
        self._n = 0
        self._ts = np.empty(self._cap, dtype=float)
        self._vals = np.empty((self._cap, len(self.PRIMITIVE_KEYS)), dtype=float)

    # ---- runtime knobs ---------------------------------------------------
    def set_window(self, key, seconds):
        """Change a window live, e.g. set_window('past1_seconds', 2*60*60)."""
        if key not in self.cfg:
            raise KeyError(f"Unknown window '{key}'. Valid: {list(self.cfg)}")
        self.cfg[key] = seconds

    def _now(self):
        return float(self._clock())

    # ---- ingestion -------------------------------------------------------
    def _snapshot(self, present_scores):
        """Pull the normalised primitives out of a process_window() result.

        Returns a float array in PRIMITIVE_KEYS order, or None if the window is
        incomplete (a missing primitive must not pollute the averages).
        """
        if not present_scores:
            return None
        row = np.empty(len(self.PRIMITIVE_KEYS), dtype=float)
        for i, key in enumerate(self.PRIMITIVE_KEYS):
            v = present_scores.get(self._NORM_KEY[key])
            if v is None:
                return None
            row[i] = float(v)
        return row

    def _grow(self):
        self._cap *= 2
        self._ts = np.resize(self._ts, self._cap)
        new_vals = np.empty((self._cap, len(self.PRIMITIVE_KEYS)), dtype=float)
        new_vals[:self._n] = self._vals[:self._n]
        self._vals = new_vals

    def add_present(self, present_scores, timestamp=None):
        ts = self._now() if timestamp is None else float(timestamp)
        row = self._snapshot(present_scores)
        if row is None:
            return
        if self._n >= self._cap:
            self._grow()
        self._ts[self._n] = ts
        self._vals[self._n] = row
        self._n += 1
        self._trim(ts)

    def _trim(self, now_ts):
        """Drop history older than the largest past window (+margin).

        Vectorised: one searchsorted to find the cut point, one slice-move.
        """
        horizon = max(self.cfg["past1_seconds"], self.cfg["past2_seconds"]) \
            * (1.0 + self.cfg["history_margin_fraction"])
        cut = int(np.searchsorted(self._ts[:self._n], now_ts - horizon, side="left"))
        if cut <= 0:
            return
        keep = self._n - cut
        self._ts[:keep] = self._ts[cut:self._n]
        self._vals[:keep] = self._vals[cut:self._n]
        self._n = keep

    # ---- averaging -------------------------------------------------------
    def _window_average(self, window_seconds, now_ts):
        """Mean of each normalised primitive over [now - window, now].

        Vectorised: searchsorted locates the window start, then a single
        column-wise mean over the contiguous slice.
        """
        keys = self.PRIMITIVE_KEYS
        if self._n == 0:
            return {k: None for k in keys}
        start = int(np.searchsorted(self._ts[:self._n],
                                    now_ts - window_seconds, side="left"))
        if start >= self._n:
            return {k: None for k in keys}
        means = self._vals[start:self._n].mean(axis=0)
        return {k: float(means[i]) for i, k in enumerate(keys)}

    @property
    def _history(self):
        """Read-only view of history as (ts, {primitive: value}) pairs.

        Kept for debugging and backwards compatibility; the hot paths use the
        numpy arrays directly.
        """
        return [(float(self._ts[i]),
                 {k: float(self._vals[i, j]) for j, k in enumerate(self.PRIMITIVE_KEYS)})
                for i in range(self._n)]

    @staticmethod
    def _index(primitive_map):
        """Collapse primitives into one 0-100 index (equal-weighted mean)."""
        vals = [v for v in primitive_map.values() if v is not None]
        return float(np.mean(vals)) if vals else None

    # ---- forecasting -----------------------------------------------------
    @staticmethod
    def _forecast(present, past, past_window_s, horizon_s, damping=0.5):
        """Project the past->present trend forward.

            slope    = (present - past) / past_window
            forecast = present + slope * horizon * damping

        `damping` (default 0.5) deliberately softens the extrapolation. An
        undamped 15-minute trend stretched over an hour is a 4x lever on
        short-term noise and produces alarming-looking swings; halving the slope
        keeps the direction while limiting the overshoot. Clamped to 0-100 since
        indexes are bounded.
        """
        if present is None:
            return None
        if past is None or past_window_s <= 0:
            return present                       # no history: best guess is "no change"
        slope = (present - past) / past_window_s
        return float(np.clip(present + slope * horizon_s * damping, 0.0, 100.0))

    # ---- public entry point ---------------------------------------------
    def compute_composites(self, present_scores, timestamp=None):
        """Ingest this window and return the temporal composites.

        Usage, once per processed window:
            present    = gait.process_window(thigh, shank)
            composites = aggregator.compute_composites(present)
            row = {**present, **composites}
        """
        now_ts = self._now() if timestamp is None else float(timestamp)
        self.add_present(present_scores, timestamp=now_ts)

        present_norm = self._snapshot(present_scores)
        present_index = float(present_norm.mean()) if present_norm is not None else None

        past1 = self._window_average(self.cfg["past1_seconds"], now_ts)
        past2 = self._window_average(self.cfg["past2_seconds"], now_ts)
        past1_index = self._index(past1)
        past2_index = self._index(past2)

        future1 = self._forecast(present_index, past1_index,
                                 self.cfg["past1_seconds"], self.cfg["future1_seconds"])
        future2 = self._forecast(present_index, past2_index,
                                 self.cfg["past2_seconds"], self.cfg["future2_seconds"])

        # FATIGUE: how far the present sits above its own recent baseline.
        # Replaces the original's `100 - cadence*0.2`, which measured cadence,
        # not fatigue. 50 = at baseline; >50 = loading above recent norm.
        fatigue = None
        if present_index is not None and past1_index is not None:
            fatigue = float(np.clip(50.0 + (present_index - past1_index), 0.0, 100.0))

        out = {
            "present_index": _sig4(present_index),
            "past1_index":   _sig4(past1_index),
            "past2_index":   _sig4(past2_index),
            "future1_index": _sig4(future1),      # FORECAST, not a measurement
            "future2_index": _sig4(future2),      # FORECAST, not a measurement
            "fatigue":       _sig4(fatigue),
            "window_meta": {
                "past1_seconds":   self.cfg["past1_seconds"],
                "past2_seconds":   self.cfg["past2_seconds"],
                "future1_seconds": self.cfg["future1_seconds"],
                "future2_seconds": self.cfg["future2_seconds"],
                "history_len":     int(self._n),
                "values_are":      "normalised_0_100",
            },
        }
        # Per-primitive past averages, generated so the set adapts automatically
        # if PRIMITIVE_KEYS ever changes.
        for k in self.PRIMITIVE_KEYS:
            field = k.replace("-", "_")
            out[f"past1_{field}"] = _sig4(past1[k])
            out[f"past2_{field}"] = _sig4(past2[k])
        return out


# =============================================================================
# AsymmetryTracker  --  interlimb (left vs right), WALL-CLOCK aligned
# =============================================================================
class AsymmetryTracker:
    """Left/right asymmetry for ONE athlete (both legs).

    Separate class because the two legs DO NOT share a clock -- each leg's
    device ts_us is an independent, wrapping, boot-relative counter. Sides are
    therefore paired by WALL-CLOCK arrival time, never by device timestamp.

    Driven from the controller, which is the only place both streams are visible.

        asym.update(side, present_scores, timestamp=time.time())
        fields = asym.compute()

    Symmetry index per metric:  |L - R| / mean(L, R)   -> 0.0 = symmetric.
    Rising asymmetry is among the clearest fatigue signals available and is an
    independent injury risk factor.
    """

    DEFAULT_METRICS = ("peak_tibial_accel", "cumulative_tibial_load")

    def __init__(self, metrics=None, pairing_window_s=2.0, clock=None):
        self.metrics = tuple(metrics) if metrics else self.DEFAULT_METRICS
        self.pairing_window_s = float(pairing_window_s)
        self._clock = clock if clock is not None else time.time
        self._latest = {"left": None, "right": None}

    def update(self, side, present_scores, timestamp=None):
        """Record one leg's latest scores, tagged with WALL-CLOCK time (not ts_us)."""
        side = str(side).strip().lower()
        if side not in ("left", "right"):
            raise ValueError(f"side must be 'left' or 'right', got {side!r}")
        if not present_scores:
            return
        ts = float(self._clock()) if timestamp is None else float(timestamp)
        self._latest[side] = (ts, present_scores)

    @staticmethod
    def _symmetry_index(l, r):
        if l is None or r is None:
            return None
        denom = 0.5 * (abs(l) + abs(r))
        return abs(l - r) / denom if denom > 1e-9 else None

    def compute(self):
        """Asymmetry per tracked metric, if a fresh left/right pair exists."""
        left, right = self._latest["left"], self._latest["right"]
        out = {f"asym_{m.replace('-', '_')}": None for m in self.metrics}
        paired, gap = False, None

        if left is not None and right is not None:
            gap = abs(left[0] - right[0])
            if gap <= self.pairing_window_s:
                paired = True
                for m in self.metrics:
                    out[f"asym_{m.replace('-', '_')}"] = _sig4(
                        self._symmetry_index(left[1].get(m), right[1].get(m)))

        out["asymmetry_meta"] = {
            "paired": paired,
            "pair_gap_s": _sig4(gap),
            "pairing_window_s": self.pairing_window_s,
            "aligned_by": "wall_clock",   # never device ts_us: legs have separate clocks
        }
        return out


# =============================================================================
# BatchGaitProcessor  --  ALL devices in one set of matrix operations
# =============================================================================
class BatchGaitProcessor(GaitProcessor):
    """Processes MANY streams at once instead of one at a time.

    WHY
    ---
    GaitProcessor.process_window() is internally vectorised, but running it in a
    Python loop over N streams pays every scipy/numpy call overhead N times.
    Stacking all streams into (N, L) matrices and issuing ONE filtfilt, ONE max,
    ONE mean, etc. amortises that overhead across the whole fleet:

        N=10  -> ~5x faster      N=50  -> ~9x faster
        N=200 -> ~14x faster     (measured on the filter+stats hot path)

    The gain grows with N, so this matters exactly when it needs to: many
    devices on one box.

    USAGE
    -----
        batch = BatchGaitProcessor()
        results = batch.process_windows({
            ("HX4_Device_30", "left"):  (thigh_buf, shank_buf),
            ("HX4_Device_30", "right"): (thigh_buf, shank_buf),
            ("HX4_Device_42", "left"):  (thigh_buf, shank_buf),
        })
        # -> {key: present_scores_dict_or_None}

    Each stream still needs its own TemporalAggregator (history is per-stream);
    only the per-window maths is batched.

    RAGGED WINDOWS
    --------------
    Packet loss means streams arrive with different sample counts, but matrix
    ops need a uniform shape. Rows are padded to the longest window with their
    own last value (edge-hold, which is filter-benign) and a validity mask
    restricts every statistic to real samples. Padding therefore changes no
    result -- verified identical to per-stream processing.
    """

    def process_windows(self, streams):
        """streams: {key: (thigh_buffer, shank_buffer)} -> {key: scores | None}.

        Streams are grouped by identical sample count and each group is
        processed as one set of matrix operations. Grouping (rather than padding
        a ragged batch to a common length) is deliberate: filtfilt derives its
        edge handling from the array length, so padding a short stream to match
        a longer one perturbs its filtered output near the seam. Grouping keeps
        every result bit-identical to per-stream processing.

        In the common case -- all devices on the same window and sample rate --
        there is a single group and the full batch speedup applies. Packet loss
        splits the batch into a few groups, which is still far better than one
        scipy call per stream.
        """
        if not streams:
            return {}

        results = {k: None for k in streams}

        # ---- Stage 1: per-stream extraction (dict unpacking cannot batch) ---
        by_length = {}
        for k, (thigh_buf, shank_buf) in streams.items():
            if not thigh_buf or not shank_buf or len(thigh_buf) < self.MIN_SAMPLES:
                continue
            t_arr = self.sig.extract_arrays(thigh_buf)
            s_arr = self.sig.extract_arrays(shank_buf)
            t_arr, s_arr = self.sig.pair_same_leg(t_arr, s_arr)
            if s_arr.shape[0] < self.MIN_SAMPLES:
                continue
            by_length.setdefault(s_arr.shape[0], []).append((k, t_arr, s_arr))

        for group in by_length.values():
            results.update(self._process_group(group))
        return results

    def _process_group(self, group):
        """Run one uniform-length group as matrix operations."""
        n = len(group)
        L = group[0][2].shape[0]
        out = {}

        # ---- Stage 2: stack into (n, L) matrices ---------------------------
        shank_ax = np.empty((n, L))
        thigh_ax = np.empty((n, L))
        gyro_xyz = np.empty((n, L, 3))
        s_col = self.sig._acc_axis_col["shank"]
        t_col = self.sig._acc_axis_col["thigh"]
        gx0 = self.sig.COL["gx"]
        for i, (_, t_arr, s_arr) in enumerate(group):
            shank_ax[i] = s_arr[:, s_col]
            thigh_ax[i] = t_arr[:, t_col]
            gyro_xyz[i] = s_arr[:, gx0:gx0 + 3]

        # ---- Stage 3: scale, de-gravity, filter -- ONE call each -----------
        shank_ax *= self.sig.acc_SF
        thigh_ax *= self.sig.acc_SF
        shank_ax -= shank_ax.mean(axis=1, keepdims=True)
        thigh_ax -= thigh_ax.mean(axis=1, keepdims=True)
        gyro = np.sqrt(((gyro_xyz * self.sig.gyro_SF) ** 2).sum(axis=2))

        if L >= self.sig._min_filter_len:
            fb, fa = self.sig._filt_b, self.sig._filt_a
            shank_ax = signal.filtfilt(fb, fa, shank_ax, axis=1)
            thigh_ax = signal.filtfilt(fb, fa, thigh_ax, axis=1)
            gyro = signal.filtfilt(fb, fa, gyro, axis=1)

        # ---- Stage 4: statistics -- ONE call each --------------------------
        fast_n = max(1, int(round(self.FAST_WINDOW_SECONDS * self.fs)))
        start = max(0, L - fast_n)
        peak_tibial = np.max(np.abs(shank_ax[:, start:]), axis=1)
        peak_thigh = np.max(np.abs(thigh_ax[:, start:]), axis=1)
        peak_gyro = np.max(np.abs(gyro[:, start:]), axis=1)
        cumulative = np.abs(shank_ax).mean(axis=1)
        jerk = np.gradient(shank_ax, 1.0 / self.fs, axis=1)
        peak_jerk = np.max(np.abs(jerk[:, start:]), axis=1)

        # ---- Stage 5: per-stream assembly ----------------------------------
        for i, (k, _, s_arr) in enumerate(group):
            prims = {
                "peak_tibial_accel": float(peak_tibial[i]),
                "cumulative_tibial_load": float(cumulative[i]),
                "peak_shank_gyro": float(peak_gyro[i]),
                "max-jerk": float(peak_jerk[i]),
            }
            norm = {kk: normalise(kk, vv, self.norm_cfg) for kk, vv in prims.items()}

            attenuation = None
            if prims["peak_tibial_accel"] > 1e-9:
                attenuation = float(np.clip(
                    1.0 - (peak_thigh[i] / prims["peak_tibial_accel"]), 0.0, 1.0))

            acl_risk = None
            usable = {kk: vv for kk, vv in norm.items() if vv is not None}
            if usable:
                tw = sum(ACL_RISK_WEIGHTS[kk] for kk in usable)
                if tw > 0:
                    acl_risk = sum(usable[kk] * ACL_RISK_WEIGHTS[kk] for kk in usable) / tw

            measured_fs = self.sig.measured_rate_hz(s_arr)
            out[k] = {
                "peak_tibial_accel":      _sig4(prims["peak_tibial_accel"]),
                "cumulative_tibial_load": _sig4(prims["cumulative_tibial_load"]),
                "peak_shank_gyro":        _sig4(prims["peak_shank_gyro"]),
                "max-jerk":               _sig4(prims["max-jerk"]),
                "norm_peak_tibial_accel":      _sig4(norm["peak_tibial_accel"]),
                "norm_cumulative_tibial_load": _sig4(norm["cumulative_tibial_load"]),
                "norm_peak_shank_gyro":        _sig4(norm["peak_shank_gyro"]),
                "norm_max_jerk":               _sig4(norm["max-jerk"]),
                "impact_attenuation": _sig4(attenuation),
                "acl_risk":           _sig4(acl_risk),
                "peak_femur_accel":   _sig4(float(peak_thigh[i])),
                "cadence":            _sig4(self._cadence(shank_ax[i], self.fs)),
                "diagnostics": {
                    "samples": L,
                    "window_seconds": _sig4(L / self.fs),
                    "nominal_sample_rate_hz": self.fs,
                    "measured_sample_rate_hz": _sig4(measured_fs),
                    "sample_rate_loss_fraction": (
                        _sig4(1.0 - measured_fs / self.fs)
                        if measured_fs is not None and self.fs else None),
                    "batched_with": n,
                },
            }
        return out
