"""Signal statistics for CSV files decoded from NYKS SD logs by bin2csv.py.

A unique sensor is identified by the pair (device_id, sensor_id).

Two outputs are available, selected with --plot:

  stats  (default)  A multi-page PDF of GENERAL signal statistics, valid for any
                    recording whatever motion it contains: sensor inventory and
                    share of data points, sampling frequency and dropout, the
                    noise floor isolated above the human-movement band, resting
                    geometry, gyro bias and dynamic-range use - every figure as a
                    chart rather than a wall of numbers - with the dataset
                    description as the closing pages.

  tap               The tap-order CALIBRATION plot. Only meaningful for a
                    recording made specifically to identify the sensors, in which
                    every unit was struck a known number of times, one unit at a
                    time, in a known order. It is what establishes the anatomical
                    mapping in SENSOR_MAP; it says nothing about ordinary motion
                    data and should not be run on it.

Both print their numbers to the terminal as well.

Units and full scale come from the `<csv>.meta.json` sidecar that bin2csv.py
writes beside every CSV (the accel/gyro full scale is per file - the firmware
latches whatever CONFIG.TXT asked for). Without a sidecar, pass
--accel-fs-g / --gyro-fs-dps / --odr-hz (and --raw if the CSV holds counts).

The technical description of the dataset - column dictionary, units and the
physical meaning of each signal - is the DATA_DESCRIPTION constant below. It is
held as a single constant rather than duplicated as a `#` comment block so the
text in the source, the text the program prints and the text in the PDF can
never drift apart. Print it with `--describe-only`; suppress it with
`--no-describe`.

Usage:
    python sensor_stats.py                          # every *.csv beside this script
    python sensor_stats.py LOG_0001.csv [more.csv ...]
    python sensor_stats.py --plot stats             # general statistics PDF (default)
    python sensor_stats.py --plot tap               # tap-order calibration PNG
    python sensor_stats.py --plot both
    python sensor_stats.py --map 7:1=right,femoral --map 7:2=right,crural LOG_0001.csv
    python sensor_stats.py --describe-only > DATA_DESCRIPTION.txt

Requires: pandas, numpy (matplotlib for the figures).
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

DEVICE_COL = "device_id"
SENSOR_COL = "sensor_id"
TIME_COL = "t_us"
UTC_COL = "unix_us"
SEQ_COL = "seq"
ACCEL_COLS = ["ax", "ay", "az"]
GYRO_COLS = ["gx", "gy", "gz"]
RAIL_COUNTS = 32767          # i16 full-scale: a sample at the rail is censored

# Fallback full-scale ranges, used ONLY when a CSV has no .meta.json sidecar
# and nothing was given on the command line (firmware 1.1.0 defaults).
DEFAULT_ACCEL_FS_G = 32.0
DEFAULT_GYRO_FS_DPS = 4000.0
DEFAULT_ODR_HZ = 6400.0

# Tunables (overridable from the command line).
TS_OUTLIER_US = 1_000_000    # a sample whose clock deviates this far from its
                             # neighbours' rolling median is a corrupt timestamp
GAP_US = 1_000               # an interval longer than this is a dropout
Z_THRESHOLD = 15.0           # candidate spike: this many robust sigma over baseline
REFRACTORY_US = 120_000      # samples closer than this belong to the same impact
CROSSTALK_US = 150_000       # peaks this close on different sensors = one physical tap

# dataviz reference palette (categorical slots 1-4, light surface) + chart chrome.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
           "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
INK, INK_2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
ORDINALS = ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th"]


def ordinal(i):
    return ORDINALS[i] if i < len(ORDINALS) else f"{i + 1}th"


def fmt_key(key):
    return f"({key[0]}, {key[1]})"


# --------------------------------------------------------------------------
# Anatomical placement. One device (one log file) is one sleeve; its two
# sensors are the femoral and crural units. Which sensor_id is which, and which
# leg a device_id is on, is established by the tap-calibration sequence
# (--plot tap) and recorded here or given with --map DEV:SID=SIDE,SEGMENT.
# Never infer segment from sensor_id alone - it may differ between sleeves.
# --------------------------------------------------------------------------

SENSOR_MAP = {
    # (device_id, sensor_id): {"side": "right", "segment": "femoral", "tap_order": 1},
}

SEGMENT_LABEL = {"femoral": "femoral (thigh)", "crural": "crural (shank / tibial)"}
SEGMENT_SHORT = {"femoral": "thigh", "crural": "shank"}


def _complete_map(m):
    """Fill label/short from side+segment so users only have to give those."""
    for key, info in m.items():
        side, seg = info.get("side", "?"), info.get("segment", "?")
        info.setdefault("label", f"{side} {SEGMENT_LABEL.get(seg, seg)}")
        info.setdefault("short", f"{side[:1].upper()} {SEGMENT_SHORT.get(seg, seg)}")
        info.setdefault("tap_order", 99)
    return m


_complete_map(SENSOR_MAP)


def seg_label(key):
    return SENSOR_MAP.get(key, {}).get("label", "placement not set")


def seg_short(key):
    return SENSOR_MAP.get(key, {}).get("short", f"S{key[1]}")


def sort_key(key):
    return (SENSOR_MAP.get(key, {}).get("tap_order", 99), key)


DATA_DESCRIPTION = """\
================================================================================
DATASET DESCRIPTION - knee-sleeve inertial capture (2 x 6-axis IMU per sleeve)
Audience: biomechanics engineers and automated analysis agents.
================================================================================

WHAT THIS FILE IS
--------------------------------------------------------------------------------
6-axis inertial data from the two IMUs of one sensor-embedded knee sleeve,
decoded from the logger's SD binary format (LOG_NNNN.BIN, "NYKS" v1) by
bin2csv.py. One .BIN file is one device and one session. Every numeric field is
the sensor's own reading scaled to physical units with the full-scale range the
firmware recorded in that file's header: no filtering, calibration or
resampling has been applied. Rows from both sensors are interleaved in
acquisition order and are separated by the (device_id, sensor_id) pair. A
`<name>.meta.json` sidecar beside the CSV carries the file header (firmware,
session, ODR, full scale) and the decoder's integrity verdict.

COLUMN DICTIONARY
--------------------------------------------------------------------------------
  device_id     u8   Logger identifier from the file header. One device = one
                     sleeve = one leg. Constant within a file.
  sensor_id     u8   IMU index on that device, 1 or 2. Combined with device_id
                     it identifies the physical sensor; on its own it does not.
  seq           u32  Sequence number of the 4 KB block the sample came from.
                     Global and monotonic across both sensors and the time-sync
                     and session-end blocks, so a missing value is a lost block.
  t_us          u64  Capture time in microseconds on the device's own clock
                     (block base_ts_us + per-sample dt_us). Per device, not
                     global. Does not wrap.
  unix_us       u64  t_us mapped to UTC microseconds through the file's SNTP
                     time-sync block: unix_us = sync.unix_us + (t_us -
                     sync.esp_us). Empty when the file holds no sync block.
  ax, ay, az    f64  Specific force along the sensor's local X/Y/Z axes, in g.
                     Full scale is per file (header accel_fs_g; default +/-32 g).
  gx, gy, gz    f64  Angular rate about the sensor's local X/Y/Z axes, in deg/s.
                     Full scale is per file (header gyro_fs_dps; default
                     +/-4000 deg/s).

  With `bin2csv.py --raw` the six signal columns are the i16 counts instead
  and the sidecar says "units": "raw"; g = counts * accel_fs_g / 32768 and
  deg/s = counts * gyro_fs_dps / 32768.

SENSOR IDENTITY AND PLACEMENT
--------------------------------------------------------------------------------
  (device_id, sensor_id)   side    segment    placement
  ----------------------   -----   --------   ---------------------------
  Established per rig by the tap-calibration recording (--plot tap) and
  recorded in SENSOR_MAP or given with --map. The sensor_id -> segment mapping
  is NOT guaranteed to be the same on every sleeve. Code that keys on
  sensor_id alone can silently swap thigh and shank. Always resolve the pair.

UNITS AND CONVERSION
--------------------------------------------------------------------------------
  a_ms2      = a_g * 9.80665                m/s^2
  omega_rads = omega_dps * pi / 180.0       rad/s
  1 LSB      = accel_fs_g / 32768           g       (0.98 mg at +/-32 g)
  1 LSB      = gyro_fs_dps / 32768          deg/s   (0.12 deg/s at +/-4000 deg/s)

  A sensor at rest reads |accel| = 1 g. Each unit's own measured resting
  magnitude includes its gain error; dividing by that measured value instead of
  1.000 removes per-unit gain error. A sample at +/-(32767/32768) * full scale
  has saturated and is censored, not measured. The wide default ranges buy
  impact headroom at the price of coarse quantisation: the noise floor of a
  still sensor is a few LSB, so treat sub-LSB structure as quantisation.

PHYSICAL MEANING OF THE SIGNALS
--------------------------------------------------------------------------------
  - The accelerometer measures SPECIFIC FORCE, not acceleration. It reports the
    sum of gravity and linear acceleration: at rest it reads 1 g pointing
    opposite gravity, and in free fall it reads zero. Gravity must be removed
    using an orientation estimate before the output represents limb acceleration.
  - Because gravity is always present it doubles as an absolute vertical
    reference, so segment roll and pitch are observable without drift.
  - The gyroscope measures ANGULAR RATE about the sensor's own axes. Integrating
    it yields orientation change, but any bias integrates into unbounded drift,
    so it must be fused with the gravity reference rather than integrated alone.
    The static bias is the sensor's median output while it is still.
  - There is no magnetometer. Rotation about the gravity vector (heading / yaw)
    therefore has no absolute reference and will drift. Use a 6-axis
    gravity-aided orientation filter and treat heading as relative only.
  - Axes are SENSOR-LOCAL. The rotation from sensor axes to anatomical axes is
    not recorded in the file and must be established by calibration before any
    joint angle is meaningful.
  - The two sensors of one device share its clock and are directly comparable
    sample-for-sample. Two devices (two sleeves) are not hardware-synchronised;
    unix_us aligns them to SNTP accuracy - a few tens of milliseconds at best -
    so cross-leg timing carries an unknown offset of that order.
  - Samples are read from the IMU FIFO in bursts of up to 290 and timestamped
    per sample, so t_us is close to uniform at the ODR (6400 Hz by default) but
    not exactly. A dropped 4 KB block (a seq gap) is a ~45 ms hole. Resample
    onto a uniform grid against t_us before any filtering, differentiation,
    integration or spectral analysis, and carry a validity mask across holes.

WHAT THE INSTRUMENTED CHAIN SUPPORTS
--------------------------------------------------------------------------------
  One sleeve spans a two-segment model of one leg, thigh and shank:
  - KNEE flexion/extension is directly observable as the relative orientation
    of the femoral and crural units.
  - Orientation relative to gravity is observable for both segments, giving
    segment inclination through the gait cycle.
  - Gait events such as heel strike and toe off are recoverable from shank
    angular-rate and acceleration transients; the 6.4 kHz rate resolves the
    impact itself, not just its envelope.
  - Two sleeves give left-right symmetry measures, aligned through unix_us.
  - HIP angle needs a pelvis reference and ANKLE angle needs a foot sensor.
    Neither segment is instrumented, so those joints are not directly observable.
================================================================================
"""


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def load_meta(csv_path, args):
    """Full-scale, ODR and integrity from the sidecar, else from the flags."""
    side = csv_path.with_suffix(".meta.json")
    meta = {}
    if side.exists():
        meta = json.loads(side.read_text())
    meta.setdefault("accel_fs_g", args.accel_fs_g or DEFAULT_ACCEL_FS_G)
    meta.setdefault("gyro_fs_dps", args.gyro_fs_dps or DEFAULT_GYRO_FS_DPS)
    meta.setdefault("odr_hz", args.odr_hz or DEFAULT_ODR_HZ)
    meta.setdefault("units", "raw" if args.raw else "physical")
    if args.accel_fs_g:
        meta["accel_fs_g"] = args.accel_fs_g
    if args.gyro_fs_dps:
        meta["gyro_fs_dps"] = args.gyro_fs_dps
    if args.odr_hz:
        meta["odr_hz"] = args.odr_hz
    meta["accel_lsb_g"] = meta["accel_fs_g"] / 32768.0
    meta["gyro_lsb_dps"] = meta["gyro_fs_dps"] / 32768.0
    meta["accel_rail_g"] = RAIL_COUNTS * meta["accel_lsb_g"]
    meta["gyro_rail_dps"] = RAIL_COUNTS * meta["gyro_lsb_dps"]
    meta["has_sidecar"] = side.exists()
    return meta


def load(csv_path, meta):
    dtypes = {DEVICE_COL: "uint8", SENSOR_COL: "uint8", SEQ_COL: "uint32",
              TIME_COL: "uint64", UTC_COL: "float64"}
    df = pd.read_csv(csv_path, dtype={k: v for k, v in dtypes.items()})
    missing = [c for c in (DEVICE_COL, SENSOR_COL) if c not in df.columns]
    if missing:
        raise SystemExit(
            f"{csv_path}: missing required column(s): {', '.join(missing)}\n"
            f"  found columns: {list(df.columns)}"
        )
    if meta["units"] == "raw":
        for c in ACCEL_COLS:
            if c in df.columns:
                df[c] = df[c].astype(float) * meta["accel_lsb_g"]
        for c in GYRO_COLS:
            if c in df.columns:
                df[c] = df[c].astype(float) * meta["gyro_lsb_dps"]
    return df


# --------------------------------------------------------------------------
# 1-4: sensor inventory
# --------------------------------------------------------------------------

def inventory(df):
    """Rows per (device_id, sensor_id), plus the count of unusable rows."""
    ids = df[[DEVICE_COL, SENSOR_COL]]
    usable = ids.notna().all(axis=1)
    counts = (df[usable].groupby([DEVICE_COL, SENSOR_COL]).size()
              .sort_index().to_dict())
    counts = {(int(k[0]), int(k[1])): int(v) for k, v in counts.items()}
    return counts, int((~usable).sum())


def inventory_lines(df, counts, skipped):
    """The 1-4 breakdown as text lines, shared by the terminal and the figure."""
    total = len(df)
    counted = sum(counts.values())
    lines = [
        f"Total rows (data points) in file : {total:,}",
    ]
    if skipped:
        lines.append(f"Rows skipped (blank id fields)   : {skipped:,}")
    lines += [
        f"Data points attributed to sensors: {counted:,}",
        "",
        f"1) Unique sensors: {len(counts)}",
        "",
        "2/3/4) Per-sensor breakdown",
        f"  {'device_id':>9}  {'sensor_id':>9}  {'data_points':>13}  {'percent':>8}"
        f"  {'placement':<29}",
        f"  {'-' * 9}  {'-' * 9}  {'-' * 13}  {'-' * 8}  {'-' * 29}",
    ]
    for key in sorted(counts, key=sort_key):
        n = counts[key]
        dev, sen = key
        pct = (n / counted * 100) if counted else 0.0
        lines.append(f"  {dev:>9}  {sen:>9}  {n:>13,}  {pct:>7.3f}%  {seg_label(key):<29}")
    lines += [
        f"  {'-' * 9}  {'-' * 9}  {'-' * 13}  {'-' * 8}  {'-' * 29}",
        f"  {'TOTAL':>9}  {'':>9}  {counted:>13,}  {100.0 if counted else 0.0:>7.3f}%",
    ]
    return lines


def report_inventory(df, counts, skipped):
    for line in inventory_lines(df, counts, skipped):
        print(line)
    print()


def meta_lines(meta):
    lines = ["File header and decoder verdict"]
    if not meta.get("has_sidecar"):
        lines.append("  (no .meta.json sidecar - full scale taken from the command line "
                     "or the firmware defaults)")
    lines.append(f"  firmware {meta.get('fw', '?')}  device {meta.get('device_id', '?')}  "
                 f"session {meta.get('session_id', '?')}  ODR {meta['odr_hz']:,.0f} Hz  "
                 f"+/-{meta['accel_fs_g']:g} g  +/-{meta['gyro_fs_dps']:g} deg/s  "
                 f"units: {meta['units']}")
    if "blocks_valid" in meta:
        lines.append(f"  blocks {meta['blocks_valid']:,} valid / {meta['blocks_bad']} bad CRC, "
                     f"{meta['seq_gaps']} seq gaps, {meta['fifo_overflows']} FIFO overflows, "
                     f"{'CLEAN' if meta['clean_end'] else 'DIRTY'} end")
    if meta.get("time_sync"):
        u = meta["time_sync"][0]["unix_us"] / 1e6
        lines.append(f"  UTC sync: {datetime.fromtimestamp(u, timezone.utc):%Y-%m-%d %H:%M:%S}Z "
                     f"({len(meta['time_sync'])} sync block(s))")
    else:
        lines.append("  UTC sync: none (unix_us empty)")
    return lines


# --------------------------------------------------------------------------
# signal conditioning
# --------------------------------------------------------------------------

def build_signals(df, meta, ts_outlier_us=TS_OUTLIER_US, gap_us=GAP_US):
    """Per sensor: cleaned, time-sorted accel/gyro in g and deg/s, |accel| and
    its robust z-score.

    Corrupt timestamps (a clock value far off its neighbours') are dropped
    before sorting, so one bad sample cannot smear the whole trace.
    """
    signals = {}
    a_rail, g_rail = meta["accel_rail_g"] * 0.9999, meta["gyro_rail_dps"] * 0.9999
    for key, g in df.groupby([DEVICE_COL, SENSOR_COL]):
        key = (int(key[0]), int(key[1]))
        ts = g[TIME_COL].to_numpy(dtype=np.int64)
        rolling_med = (pd.Series(ts).rolling(11, center=True, min_periods=1)
                       .median().to_numpy())
        good = np.abs(ts - rolling_med) <= ts_outlier_us

        accel = g[ACCEL_COLS].to_numpy(dtype=float)[good]
        ts = ts[good]
        order = np.argsort(ts, kind="stable")
        ts, accel = ts[order], accel[order]

        mag = np.sqrt((accel ** 2).sum(axis=1))
        baseline = float(np.median(mag))                    # gravity + static bias
        mad = float(np.median(np.abs(mag - baseline))) * 1.4826
        mad = mad if mad > 0 else meta["accel_lsb_g"]

        gyro = (g[GYRO_COLS].to_numpy(dtype=float)[good][order]
                if all(c in g.columns for c in GYRO_COLS) else None)
        seq = g[SEQ_COL].to_numpy()[good][order] if SEQ_COL in g.columns else None
        dt = np.diff(ts)

        dt_med = float(np.median(dt)) if dt.size else float("nan")
        at_nominal = (float(((dt >= 0.8 * dt_med) & (dt <= 1.2 * dt_med)).mean())
                      if dt.size else float("nan"))

        signals[key] = {
            "t": ts,
            "dt": dt,
            "accel": accel,
            "gyro": gyro,
            "seq": seq,
            "mag": mag,
            "baseline": baseline,
            "mad": mad,
            "z": (mag - baseline) / mad,
            "frac_at_nominal": at_nominal,
            "n_bad_ts": int((~good).sum()),
            "n_backwards": int((np.diff(g[TIME_COL].to_numpy(dtype=np.int64)[good]) < 0).sum()),
            # signal-quality profile
            "dt_median": dt_med,
            "duration_s": float((ts[-1] - ts[0]) / 1e6) if ts.size > 1 else 0.0,
            "n_gaps": int((dt > gap_us).sum()),
            "gap_time_s": float(dt[dt > gap_us].sum() / 1e6) if dt.size else 0.0,
            "max_gap_ms": float(dt.max() / 1e3) if dt.size else 0.0,
            "n_clip_accel": int((np.abs(accel) >= a_rail).any(axis=1).sum()),
            "n_clip_gyro": int((np.abs(gyro) >= g_rail).any(axis=1).sum()) if gyro is not None else 0,
            "n_blocks": int(np.unique(seq).size) if seq is not None else 0,
        }
    return signals


# --------------------------------------------------------------------------
# 5: tap detection and ordering
# --------------------------------------------------------------------------

def find_candidates(signals, z_thresh=Z_THRESHOLD, refractory_us=REFRACTORY_US):
    """Every spike above threshold, collapsed to one peak per impact."""
    candidates = []
    for key, s in signals.items():
        t, z = s["t"], s["z"]
        hits = np.flatnonzero(z > z_thresh)
        if hits.size == 0:
            continue
        splits = np.flatnonzero(np.diff(t[hits]) > refractory_us) + 1
        for burst in np.split(hits, splits):
            peak = burst[np.argmax(z[burst])]
            candidates.append({
                "key": key,
                "t": int(t[peak]),
                "z": float(z[peak]),
                "g": float(s["mag"][peak] / s["baseline"]),
                "t_start": int(t[burst[0]]),
                "t_end": int(t[burst[-1]]),
            })
    candidates.sort(key=lambda c: c["t"])
    return candidates


def resolve_crosstalk(candidates, window_us=CROSSTALK_US):
    """One physical tap rings every sensor on the rig; keep the loudest.

    Peaks within `window_us` are treated as the same impact. A cluster never
    holds the same sensor twice, so a sensor's own successive taps cannot chain
    into one another.
    """
    clusters, current = [], []
    for c in candidates:
        chains = (current
                  and c["t"] - current[-1]["t"] <= window_us
                  and all(x["key"] != c["key"] for x in current))
        if chains:
            current.append(c)
        else:
            if current:
                clusters.append(current)
            current = [c]
    if current:
        clusters.append(current)

    winners = []
    for cluster in clusters:
        ranked = sorted(cluster, key=lambda c: c["z"], reverse=True)
        win = dict(ranked[0])
        win["runner_up_z"] = ranked[1]["z"] if len(ranked) > 1 else 0.0
        win["n_responding"] = len(cluster)
        winners.append(win)
    return winners


def select_taps(winners, keys, taps_per_sensor):
    """Keep each sensor's strongest `taps_per_sensor` impacts."""
    taps, margins = {}, {}
    for key in keys:
        mine = sorted([w for w in winners if w["key"] == key],
                      key=lambda w: w["z"], reverse=True)
        chosen = mine[:taps_per_sensor]
        rejected = mine[taps_per_sensor:]
        taps[key] = sorted(chosen, key=lambda w: w["t"])
        weakest = min((c["z"] for c in chosen), default=0.0)
        loudest_reject = max((r["z"] for r in rejected), default=0.0)
        margins[key] = (weakest / loudest_reject) if loudest_reject else float("inf")
    return taps, margins


def report_taps(signals, taps, margins, taps_per_sensor):
    """Print the tap order; return the sensor keys in chronological order."""
    tapped = {k: v for k, v in taps.items() if v}
    order = sorted(tapped, key=lambda k: np.median([t["t"] for t in tapped[k]]))

    print(f"5) Tap order  (expecting {taps_per_sensor} taps per sensor)")
    if not order:
        print("   No taps detected - check --z-thresh or the input file.")
        print()
        return order

    print(f"  {'order':>5}  {'sensor':>10}  {'taps':>5}  {'window (s)':>18}"
          f"  {'peak':>8}  {'margin':>7}  {'placement':<29}")
    print(f"  {'-' * 5}  {'-' * 10}  {'-' * 5}  {'-' * 18}  {'-' * 8}  {'-' * 7}  {'-' * 29}")
    for i, key in enumerate(order):
        ts = [t["t"] / 1e6 for t in tapped[key]]
        peak = max(t["g"] for t in tapped[key])
        margin = margins[key]
        margin_s = "inf" if margin == float("inf") else f"{margin:.1f}x"
        print(f"  {ordinal(i):>5}  {fmt_key(key):>10}  {len(ts):>5}"
              f"  {ts[0]:>8.3f} - {ts[-1]:<7.3f}  {peak:>7.1f}g  {margin_s:>7}"
              f"  {seg_label(key):<29}")

    print()
    print("  Chronological order: " + "  ->  ".join(fmt_key(k) for k in order))
    print("  As placements:       " + "  ->  ".join(seg_label(k) for k in order))
    print()

    print("  Individual taps")
    for i, key in enumerate(order):
        for j, tap in enumerate(taps[key], start=1):
            dominance = (tap["z"] / tap["runner_up_z"]) if tap["runner_up_z"] else float("inf")
            dom_s = "isolated" if dominance == float("inf") else f"{dominance:.0f}x louder"
            print(f"    {ordinal(i)} sensor {fmt_key(key)} tap {j}: "
                  f"t = {tap['t'] / 1e6:8.3f}s  peak = {tap['g']:6.1f}g  "
                  f"({tap['n_responding']} sensor(s) responded, {dom_s})")
    print()

    # Integrity notes.
    warnings = []

    documented = [k for k, v in sorted(SENSOR_MAP.items(), key=lambda kv: kv[1]["tap_order"])
                  if v.get("tap_order", 99) < 99]
    if documented and set(order) == set(documented) and order != documented:
        warnings.append("detected tap order DISAGREES with the SENSOR_MAP placement table "
                        f"({'->'.join(fmt_key(k) for k in documented)}) - the anatomical "
                        "labels in this report are wrong for this file")
    unmapped = [k for k in order if k not in SENSOR_MAP]
    if unmapped:
        warnings.append("no documented placement for: "
                        + ", ".join(fmt_key(k) for k in unmapped)
                        + " - record this order in SENSOR_MAP or pass --map")

    for key in order:
        if len(taps[key]) < taps_per_sensor:
            warnings.append(f"sensor {fmt_key(key)} has only {len(taps[key])} "
                            f"detected taps (expected {taps_per_sensor})")
        if margins[key] < 2.0:
            warnings.append(f"sensor {fmt_key(key)} tap selection is ambiguous "
                            f"(weakest kept tap only {margins[key]:.1f}x the "
                            f"loudest discarded spike)")
    spans = [(taps[k][0]["t"], taps[k][-1]["t"], k) for k in order]
    for (a0, a1, ka), (b0, b1, kb) in zip(spans, spans[1:]):
        if b0 <= a1:
            warnings.append(f"tap windows of {fmt_key(ka)} and {fmt_key(kb)} overlap "
                            f"- taps may not have been strictly one-at-a-time")

    bad_ts = {k: s["n_bad_ts"] for k, s in signals.items() if s["n_bad_ts"]}
    if bad_ts:
        warnings.append("corrupt timestamps dropped: "
                        + ", ".join(f"{fmt_key(k)}={n}" for k, n in bad_ts.items()))
    backwards = {k: s["n_backwards"] for k, s in signals.items() if s["n_backwards"]}
    if backwards:
        warnings.append("out-of-order samples re-sorted by timestamp: "
                        + ", ".join(f"{fmt_key(k)}={n}" for k, n in backwards.items()))

    if warnings:
        print("  Notes")
        for w in warnings:
            print(f"    - {w}")
        print()
    return order


# --------------------------------------------------------------------------
# 7: signal quality
# --------------------------------------------------------------------------

def quality_lines(signals, order, gap_us):
    """Live measurement of the defects catalogued in DATA_DESCRIPTION."""
    keys = order + [k for k in signals if k not in order]
    gap_ms = gap_us / 1000
    lines = [
        "Sampling and data continuity",
        f"  {'sensor':>7}  {'placement':<29}  {'nominal':>9}  {'delivered':>10}"
        f"  {'at nom.':>8}  {f'gaps>{gap_ms:g}ms':>9}  {'lost':>7}  {'max gap':>9}  {'clipped':>11}",
        f"  {'-' * 7}  {'-' * 29}  {'-' * 9}  {'-' * 10}  {'-' * 8}  {'-' * 9}"
        f"  {'-' * 7}  {'-' * 9}  {'-' * 11}",
    ]
    for key in keys:
        s = signals[key]
        nominal = 1e6 / s["dt_median"] if s["dt_median"] > 0 else float("nan")
        delivered = len(s["t"]) / s["duration_s"] if s["duration_s"] else float("nan")
        clipped = f"{s['n_clip_accel']}a/{s['n_clip_gyro']}g"
        lines.append(
            f"  {fmt_key(key):>7}  {seg_label(key):<29}  {nominal:>7.0f}Hz"
            f"  {delivered:>8.0f}Hz  {s['frac_at_nominal'] * 100:>7.1f}%"
            f"  {s['n_gaps']:>9,}  {s['gap_time_s']:>6.1f}s"
            f"  {s['max_gap_ms']:>7.1f}ms  {clipped:>11}")
    lines += [
        "",
        "  'nominal' = 1/median(dt), the rate the sensor actually converts at.",
        "  'delivered' = samples/span, degraded by lost blocks and FIFO overflow.",
        "  'at nom.' = share of intervals within +/-20% of nominal, i.e. the fraction",
        "  of the record genuinely sampled at the full rate.",
    ]
    return lines


def report_quality(signals, order, gap_us):
    for line in quality_lines(signals, order, gap_us):
        print(line)
    print()


# --------------------------------------------------------------------------
# 8: motion-agnostic noise
# --------------------------------------------------------------------------

def continuous_runs(t, max_gap_us):
    """Index groups of consecutive samples with no gap longer than max_gap_us."""
    if t.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(t) > max_gap_us) + 1
    return np.split(np.arange(t.size), breaks)


def _detrended_std(x, y):
    """Residual std of a per-window least-squares line. x: (m, n), y: (m, n, 3)."""
    ym = y.mean(axis=1, keepdims=True)
    slope = (x[:, :, None] * (y - ym)).sum(axis=1) / (x ** 2).sum(axis=1)[:, None]
    resid = y - ym - x[:, :, None] * slope[:, None, :]
    return resid.std(axis=1)


def hf_noise(signals, f_cut, max_gap_us=GAP_US, min_samples=20, chunk=8192):
    """Robust noise above f_cut, measured WITHOUT resampling.

    Human locomotion is confined to roughly DC-20 Hz, so whatever remains above
    that is instrumentation noise regardless of what the subject was doing. The
    estimator is:

      * split each sensor into gap-free bursts (no interval > max_gap_us), so no
        value is ever interpolated across a dropout - interpolation would smooth
        the signal and understate the noise floor;
      * cut each burst into windows of T = 0.44 / f_cut seconds, the length over
        which a straight-line fit removes essentially everything below f_cut;
      * linear-detrend each window and take the residual standard deviation;
      * report the MEDIAN over all windows, which rejects impacts, footfalls and
        other transients that would inflate a mean.

    Windows are a fixed number of samples (window length / median dt) so the
    fit is vectorised across windows; at 6.4 kHz that is ~140 samples each.

    Returns per sensor: per-axis accel noise in g and gyro noise in deg/s, plus
    the count of contributing windows.
    """
    window_us = 0.44e6 / f_cut
    out = {}
    for key, s in signals.items():
        t = s["t"]
        n_win = max(min_samples, int(round(window_us / s["dt_median"])))
        acc_res, gyr_res = [], []
        for run in continuous_runs(t, max_gap_us):
            m = run.size // n_win
            if m == 0:
                continue
            for c0 in range(0, m, chunk):
                idx = run[c0 * n_win:min(m, c0 + chunk) * n_win].reshape(-1, n_win)
                x = t[idx].astype(float)
                x -= x.mean(axis=1, keepdims=True)
                acc_res.append(_detrended_std(x, s["accel"][idx]))
                if s["gyro"] is not None:
                    gyr_res.append(_detrended_std(x, s["gyro"][idx]))
        acc = np.concatenate(acc_res) if acc_res else np.empty((0, 3))
        gyr = np.concatenate(gyr_res) if gyr_res else np.empty((0, 3))
        out[key] = {
            "window_ms": window_us / 1e3,
            "n_windows": int(acc.shape[0]),
            "accel_g": np.median(acc, axis=0) if acc.size else np.full(3, np.nan),
            "gyro_dps": np.median(gyr, axis=0) if gyr.size else np.full(3, np.nan),
        }
    return out


def welch_median_psd(x, fs, nperseg, max_segments=512):
    """Median-averaged Welch PSD. Median, not mean, so transients don't dominate.

    At most `max_segments` half-overlapping segments, spread evenly over the
    record, so an hour-long file costs the same as a minute-long one.
    """
    nperseg = int(min(nperseg, x.size))
    if nperseg < 16:
        return np.array([]), np.array([])
    step = nperseg // 2
    starts = np.arange(0, x.size - nperseg + 1, step)
    if starts.size == 0:
        return np.array([]), np.array([])
    if starts.size > max_segments:
        starts = starts[np.linspace(0, starts.size - 1, max_segments).astype(int)]
    win = np.hanning(nperseg)
    norm = fs * (win ** 2).sum()
    segs = np.stack([x[i:i + nperseg] for i in starts])
    segs = segs - segs.mean(axis=1, keepdims=True)
    spec = np.abs(np.fft.rfft(segs * win, axis=1)) ** 2 / norm
    spec[:, 1:-1] *= 2
    return np.fft.rfftfreq(nperseg, 1 / fs), np.median(spec, axis=0)


def session_psd(signals, fs, nperseg=8192):
    """PSD of |accel| and |gyro| over the whole session, on a uniform grid.

    This one DOES interpolate across dropouts, which suppresses high-frequency
    content - it is here to show where motion energy sits relative to the 20 Hz
    boundary, not to measure the noise floor. Use hf_noise() for that. The grid
    rate defaults to the nominal ODR so nothing is aliased down.
    """
    out = {}
    for key, s in signals.items():
        t = s["t"].astype(float)
        if t.size < 4:
            continue
        grid = np.arange(t[0], t[-1], 1e6 / fs)
        entry = {}
        for name, arr in (("accel", s["accel"]), ("gyro", s["gyro"])):
            if arr is None:
                continue
            mag = np.sqrt((arr ** 2).sum(axis=1))
            f, p = welch_median_psd(np.interp(grid, t, mag), fs, nperseg)
            entry[name] = (f, p)
        out[key] = entry
    return out


def noise_lines(signals, noise, meta, f_cut, order):
    keys = order + [k for k in signals if k not in order]
    win_ms = next(iter(noise.values()))["window_ms"] if noise else 0.0
    lines = [
        f"Motion-agnostic noise  (content above {f_cut:g} Hz; "
        f"{win_ms:.0f} ms detrended windows inside gap-free bursts, median over windows)",
        f"  {'sensor':>7}  {'placement':<29}  {'accel noise (mg RMS)':>22}"
        f"  {'gyro noise (mdeg/s RMS)':>22}  {'rest |a|':>8}  {'wins':>7}",
        f"  {'':>7}  {'':<29}  {'x':>7}{'y':>7}{'z':>8}  {'x':>7}{'y':>7}{'z':>8}"
        f"  {'':>8}  {'':>7}",
        f"  {'-' * 7}  {'-' * 29}  {'-' * 22}  {'-' * 22}  {'-' * 8}  {'-' * 7}",
    ]
    for key in keys:
        s, n = signals[key], noise[key]
        mg = n["accel_g"] / s["baseline"] * 1000.0        # g -> mg, gain-corrected
        gy = n["gyro_dps"] * 1000.0                        # deg/s -> mdeg/s
        lines.append(
            f"  {fmt_key(key):>7}  {seg_label(key):<29}"
            f"  {mg[0]:>7.2f}{mg[1]:>7.2f}{mg[2]:>8.2f}"
            f"  {gy[0]:>7.0f}{gy[1]:>7.0f}{gy[2]:>8.0f}"
            f"  {s['baseline']:>7.4f}g  {n['n_windows']:>7,}")
    lines += [
        "",
        "  Accelerometer noise is divided by each sensor's own resting |accel| (the",
        "  'rest |a|' column), so per-unit gain error is removed. One count is",
        f"  {meta['accel_lsb_g'] * 1000:.3f} mg and {meta['gyro_lsb_dps'] * 1000:.0f} mdeg/s "
        f"at this file's +/-{meta['accel_fs_g']:g} g / +/-{meta['gyro_fs_dps']:g} deg/s.",
    ]
    return lines


def bias_lines(signals, order):
    keys = order + [k for k in signals if k not in order]
    lines = [
        "Rest orientation and gyroscope bias",
        f"  {'sensor':>7}  {'placement':<29}  {'median accel (g)':>27}"
        f"  {'gyro bias (mdeg/s)':>22}  {'tilt':>7}",
        f"  {'':>7}  {'':<29}  {'x':>9}{'y':>9}{'z':>9}  {'x':>7}{'y':>7}{'z':>8}"
        f"  {'':>7}",
        f"  {'-' * 7}  {'-' * 29}  {'-' * 27}  {'-' * 22}  {'-' * 7}",
    ]
    for key in keys:
        s = signals[key]
        a = np.median(s["accel"], axis=0)
        g = (np.median(s["gyro"], axis=0) * 1000.0
             if s["gyro"] is not None else np.full(3, np.nan))
        tilt = np.degrees(np.arccos(np.clip(abs(a[2]) / np.linalg.norm(a), -1, 1)))
        lines.append(
            f"  {fmt_key(key):>7}  {seg_label(key):<29}"
            f"  {a[0]:>9.4f}{a[1]:>9.4f}{a[2]:>9.4f}"
            f"  {g[0]:>7.0f}{g[1]:>7.0f}{g[2]:>8.0f}  {tilt:>6.1f}d")
    lines += [
        "",
        "  'tilt' is the angle between the sensor +Z axis and the median gravity",
        "  vector. Gyro medians are the static bias to remove before integration.",
    ]
    return lines


def report_noise(signals, noise, meta, f_cut, order):
    print("8) Motion-agnostic noise and bias")
    for line in noise_lines(signals, noise, meta, f_cut, order):
        print(line)
    print()
    for line in bias_lines(signals, order):
        print(line)
    print()


# --------------------------------------------------------------------------
# 6: plot
# --------------------------------------------------------------------------

def max_envelope(t, y, nbins):
    """Peak-preserving decimation: the max of y within each time bin."""
    if t.size <= nbins:
        return t, y
    t0, t1 = float(t[0]), float(t[-1])
    if t1 <= t0:
        return t, y
    idx = np.clip(((t - t0) / (t1 - t0) * nbins).astype(int), 0, nbins - 1)
    peaks = np.full(nbins, -np.inf)
    np.maximum.at(peaks, idx, y)
    centers = t0 + (np.arange(nbins) + 0.5) * (t1 - t0) / nbins
    keep = np.isfinite(peaks)
    return centers[keep], peaks[keep]


def plot_tap_order(signals, taps, order, out_path, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    colors = {key: PALETTE[i % len(PALETTE)] for i, key in enumerate(order)}
    n = len(order)

    fig, axes = plt.subplots(
        n + 1, 1, figsize=(14, 2.0 * n + 2.4), sharex=True,
        gridspec_kw={"height_ratios": [0.5] + [1] * n, "hspace": 0.16},
    )
    fig.patch.set_facecolor(SURFACE)
    strip, panels = axes[0], axes[1:]

    t_lo = min(s["t"][0] for s in signals.values()) / 1e6
    t_hi = max(s["t"][-1] for s in signals.values()) / 1e6

    strip.set_facecolor(SURFACE)
    strip.set_ylim(0, 1)
    for i, key in enumerate(order):
        t0 = taps[key][0]["t"] / 1e6
        t1 = taps[key][-1]["t"] / 1e6
        pad = max(0.25, (t1 - t0) * 0.12)
        strip.add_patch(plt.Rectangle((t0 - pad, 0.08), (t1 - t0) + 2 * pad, 0.46,
                                      facecolor=colors[key], edgecolor=SURFACE,
                                      linewidth=2, zorder=3))
        strip.annotate(f"{ordinal(i)}  {fmt_key(key)}\n{seg_label(key)}",
                       xy=((t0 + t1) / 2, 0.62), ha="center", va="bottom",
                       fontsize=10, fontweight="bold", color=INK,
                       linespacing=1.5)
        if i:
            prev = taps[order[i - 1]][-1]["t"] / 1e6
            strip.annotate("", xy=(t0 - pad, 0.31), xytext=(prev + pad, 0.31),
                           arrowprops=dict(arrowstyle="->", color=MUTED,
                                           linewidth=1.2, shrinkA=0, shrinkB=0))
    strip.set_yticks([])
    strip.tick_params(bottom=False, labelbottom=False)
    strip.set_ylabel("tap\norder", rotation=0, ha="right", va="center",
                     fontsize=9.5, color=INK_2, labelpad=14)
    for side in ("top", "right", "left", "bottom"):
        strip.spines[side].set_visible(False)

    for i, (key, ax) in enumerate(zip(order, panels)):
        s = signals[key]
        color = colors[key]
        excess_g = np.maximum(s["mag"] - s["baseline"], 0.0) / s["baseline"]
        t_s, env = max_envelope(s["t"] / 1e6, excess_g, 3000)

        ax.set_facecolor(SURFACE)
        for other in order:
            o0 = taps[other][0]["t"] / 1e6
            o1 = taps[other][-1]["t"] / 1e6
            pad = max(0.25, (o1 - o0) * 0.12)
            ax.axvspan(o0 - pad, o1 + pad, color=colors[other],
                       alpha=0.30 if other == key else 0.07, zorder=0, linewidth=0)

        ax.fill_between(t_s, 0, env, color=color, linewidth=0, alpha=0.85, zorder=2)
        peak_g = max(t["g"] - 1.0 for t in taps[key]) if taps[key] else env.max()
        for j, tap in enumerate(taps[key], start=1):
            x = tap["t"] / 1e6
            y = tap["g"] - 1.0
            ax.plot([x], [y], marker="*", markersize=13, color=color,
                    markeredgecolor=SURFACE, markeredgewidth=1.6, zorder=4,
                    clip_on=False)
            ax.annotate(str(j), xy=(x, y), xytext=(0, 11), textcoords="offset points",
                        ha="center", fontsize=9, fontweight="bold", color=INK, zorder=5)

        ax.set_ylim(0, max(peak_g, env.max()) * 1.30 + 1e-9)
        ax.set_ylabel(f"{ordinal(i)}   {fmt_key(key)}\n{seg_short(key)}", rotation=0,
                      ha="right", va="center", fontsize=11, fontweight="bold",
                      color=INK, labelpad=14, linespacing=1.6)
        ax.annotate(f"peak {peak_g + 1:.0f}g", xy=(0.995, 0.86), xycoords="axes fraction",
                    ha="right", va="top", fontsize=8.5, color=MUTED)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(colors=MUTED, labelsize=9)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(AXIS)

    panels[-1].set_xlabel("time (s, device clock)", fontsize=10, color=INK_2)
    strip.set_xlim(t_lo, t_hi)

    fig.suptitle(title, x=0.5, y=0.985, fontsize=13.5, fontweight="bold", color=INK)
    fig.text(0.5, 0.945,
             "shock above rest (g), per sensor   |   "
             + "  ->  ".join(f"{ordinal(i)} {seg_short(k)} {fmt_key(k)}"
                            for i, k in enumerate(order)),
             ha="center", fontsize=10, color=INK_2)
    fig.legend(handles=[Patch(facecolor=colors[k],
                              label=f"{fmt_key(k)} {seg_label(k)} - tapped {ordinal(i)}")
                        for i, k in enumerate(order)],
               loc="lower center", ncol=min(len(order), 4), frameon=False,
               fontsize=9.5, labelcolor=INK_2, bbox_to_anchor=(0.5, -0.005))
    fig.subplots_adjust(left=0.13, right=0.985, top=0.925, bottom=0.10)
    fig.savefig(out_path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return out_path


# --------------------------------------------------------------------------

def analyse(csv_path, args):
    print("=" * 72)
    print(f"File: {csv_path}")
    print("=" * 72)

    meta = load_meta(csv_path, args)
    df = load(csv_path, meta)
    for line in meta_lines(meta):
        print(line)
    print()
    counts, skipped = inventory(df)
    report_inventory(df, counts, skipped)

    needed = [TIME_COL] + ACCEL_COLS
    if any(c not in df.columns for c in needed):
        print("5/6) Skipped - needs columns: " + ", ".join(needed))
        return

    signals = build_signals(df, meta, args.ts_outlier_us, args.gap_us)
    want_tap = args.plot in ("tap", "both")
    want_stats = args.plot in ("stats", "both")

    order = []
    if want_tap:
        candidates = find_candidates(signals, args.z_thresh, args.refractory_us)
        winners = resolve_crosstalk(candidates, args.crosstalk_us)
        taps, margins = select_taps(winners, list(signals), args.taps_per_sensor)
        order = report_taps(signals, taps, margins, args.taps_per_sensor)

        if not order:
            print("6) Calibration plot skipped - no taps detected.\n")
        else:
            out = (args.out if args.out and args.plot == "tap"
                   else csv_path.with_name(csv_path.stem + "_tap_order.png"))
            plot_tap_order(signals, taps, order, out, f"Tap order - {csv_path.name}")
            print(f"6) Calibration plot written: {out}\n")
    else:
        print("5/6) Tap-order calibration not run (--plot stats). The tap analysis is")
        print("     only meaningful for a recording where each sensor was struck a")
        print("     known number of times in a known order; run --plot tap for those.")
        print()

    ordered = order or sorted(signals, key=sort_key)
    report_quality(signals, ordered, args.gap_us)

    noise = hf_noise(signals, args.noise_above_hz, args.gap_us)
    report_noise(signals, noise, meta, args.noise_above_hz, ordered)

    if want_stats:
        fs_psd = args.psd_fs or float(np.median([1e6 / s["dt_median"] for s in signals.values()]))
        psds = session_psd(signals, fs=fs_psd)
        out = (args.out if args.out and args.plot == "stats"
               else csv_path.with_name(csv_path.stem + "_general_stats.pdf"))
        plot_general_stats(df, counts, skipped, signals, noise, psds, meta,
                           args.noise_above_hz, out, csv_path.name, fs_psd, args.gap_us)
        print(f"9) General statistics PDF written: {out}\n")


PAGE_W, PAGE_H = 11.69, 8.27          # A4 landscape, inches
MARGIN_L, MARGIN_R = 0.62, 0.45
BODY_TOP, BODY_BOTTOM = 0.775, 0.072   # figure fractions; clears the page legend
CELL_PAD_L, CELL_PAD_B = 0.068, 0.090
DESC_PT, DESC_MAX_PER_COL = 7.2, 41


def _page(plt, title, subtitle=None, page_no=None, total=None):
    """A blank page carrying the standing furniture: title, caption, folio."""
    fig = plt.figure(figsize=(PAGE_W, PAGE_H))
    fig.patch.set_facecolor(SURFACE)
    x = MARGIN_L / PAGE_W
    fig.text(x, 0.962, title, fontsize=15, fontweight="bold", color=INK, va="top")
    if subtitle:
        fig.text(x, 0.917, subtitle, fontsize=9.5, color=INK_2, va="top")
    fig.add_artist(plt.Line2D([x, 1 - MARGIN_R / PAGE_W], [0.895, 0.895],
                              color=GRID, linewidth=1.0))
    if page_no:
        fig.text(1 - MARGIN_R / PAGE_W, 0.032, f"{page_no} / {total}",
                 fontsize=8, color=MUTED, ha="right", va="bottom")
        fig.text(x, 0.032, "sensor_stats.py  ·  general signal statistics",
                 fontsize=8, color=MUTED, va="bottom")
    return fig


def _cell(fig, col, row, ncols=2, nrows=2, colspan=1, rowspan=1,
          hgap=0.055, vgap=0.115, pad_left=CELL_PAD_L, pad_bottom=CELL_PAD_B):
    """Axes on a ncols x nrows grid inside the page body."""
    x0, x1 = MARGIN_L / PAGE_W, 1 - MARGIN_R / PAGE_W
    w_total, h_total = x1 - x0, BODY_TOP - BODY_BOTTOM
    cw = (w_total - hgap * (ncols - 1)) / ncols
    ch = (h_total - vgap * (nrows - 1)) / nrows
    x = x0 + col * (cw + hgap)
    y = BODY_BOTTOM + (nrows - row - rowspan) * (ch + vgap)
    w = cw * colspan + hgap * (colspan - 1)
    h = ch * rowspan + vgap * (rowspan - 1)
    ax = fig.add_axes([x + pad_left, y + pad_bottom, w - pad_left, h - pad_bottom])
    ax.set_facecolor(SURFACE)
    return ax


def _sensor_colors(keys):
    return {k: PALETTE[i % len(PALETTE)] for i, k in enumerate(keys)}


def _bar_labels(ax, bars, values, fmt="{:.2f}", size=7.5):
    """Value on each bar, flipped below the axis for negative bars."""
    for b, v in zip(bars, values):
        if not np.isfinite(v):
            continue
        below = v < 0
        ax.annotate(fmt.format(v), xy=(b.get_x() + b.get_width() / 2, v),
                    xytext=(0, -3 if below else 2), textcoords="offset points",
                    ha="center", va="top" if below else "bottom",
                    fontsize=size, color=INK_2)


def _page_legend(fig, keys, colors):
    """One legend per page, under the rule - every panel shares these colours."""
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(facecolor=colors[k], label=f"{fmt_key(k)}  {seg_label(k)}")
                        for k in keys],
               loc="upper left", bbox_to_anchor=(MARGIN_L / PAGE_W, 0.884),
               ncol=len(keys), frameon=False, fontsize=8.5, labelcolor=INK_2,
               handlelength=1.1, handleheight=1.0, columnspacing=2.0)


# --------------------------------------------------------------------------

def _page_overview(plt, fig, df, counts, signals, meta, keys, colors):
    total = sum(counts.values())
    dur = max(s["duration_s"] for s in signals.values())
    rate = float(np.median([1e6 / s["dt_median"] for s in signals.values()]))

    # --- stat tiles --------------------------------------------------------
    tiles = [("unique sensors", f"{len(counts)}", "(device_id, sensor_id) pairs"),
             ("data points", f"{total:,}", "rows across all sensors"),
             ("duration", f"{dur:.1f} s", "longest sensor span"),
             ("nominal rate", f"{rate:,.0f} Hz",
              f"1 / median dt  (header ODR {meta['odr_hz']:,.0f} Hz)")]
    x0, x1 = MARGIN_L / PAGE_W, 1 - MARGIN_R / PAGE_W
    tw = (x1 - x0 - 0.03 * 3) / 4
    for i, (label, value, note) in enumerate(tiles):
        ax = fig.add_axes([x0 + i * (tw + 0.03), 0.700, tw, 0.135])
        ax.set_axis_off()
        ax.add_patch(plt.Rectangle((0, 0), 1, 1, transform=ax.transAxes,
                                   facecolor="#f2f1ec", edgecolor="none"))
        ax.text(0.055, 0.80, label, fontsize=8.5, color=MUTED,
                va="top", transform=ax.transAxes)
        ax.text(0.055, 0.58, value, fontsize=21, fontweight="bold", color=INK,
                va="center", transform=ax.transAxes)
        ax.text(0.055, 0.16, note, fontsize=7.5, color=MUTED,
                va="center", transform=ax.transAxes)

    # --- header / verdict line under the tiles -----------------------------
    parts = [f"fw {meta.get('fw', '?')}", f"device {meta.get('device_id', '?')}",
             f"session {meta.get('session_id', '?')}",
             f"±{meta['accel_fs_g']:g} g / ±{meta['gyro_fs_dps']:g} deg/s full scale"]
    if "blocks_valid" in meta:
        parts.append(f"{meta['blocks_valid']:,} blocks, {meta['blocks_bad']} bad CRC, "
                     f"{meta['seq_gaps']} seq gaps, {meta['fifo_overflows']} FIFO overflows, "
                     f"{'clean' if meta['clean_end'] else 'DIRTY'} end")
    if meta.get("time_sync"):
        u = meta["time_sync"][0]["unix_us"] / 1e6
        parts.append(f"UTC {datetime.fromtimestamp(u, timezone.utc):%Y-%m-%d %H:%M:%S}Z")
    else:
        parts.append("no UTC sync")
    fig.text(x0, 0.672, "   ·   ".join(parts), fontsize=8, color=MUTED, va="top")

    # --- share of data points ---------------------------------------------
    ax = fig.add_axes([x0 + 0.075, 0.215, (x1 - x0) * 0.50 - 0.075, 0.315])
    ax.set_facecolor(SURFACE)
    vals = [counts[k] for k in keys]
    pcts = [v / total * 100 for v in vals]
    ypos = np.arange(len(keys))[::-1]
    ax.barh(ypos, pcts, height=0.62, color=[colors[k] for k in keys])
    for y, k, v, p in zip(ypos, keys, vals, pcts):
        ax.annotate(f"{p:.2f}%   ({v:,})", xy=(p, y), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=8.5, color=INK_2)
    ax.set_yticks(ypos, [f"{fmt_key(k)}  {seg_short(k)}" for k in keys], fontsize=8.5)
    ax.set_xlim(0, max(pcts) * 1.45)
    ax.set_xlabel("share of all data points (%)", fontsize=9, color=INK_2)
    _titles(ax, "Data points captured per sensor",
            "an even split means no sensor is starved of logger bandwidth", wrap=58)
    _style(ax, grid_axis="x")

    # --- placement schematic ----------------------------------------------
    ax = fig.add_axes([x0 + (x1 - x0) * 0.58, 0.215, (x1 - x0) * 0.42, 0.315])
    ax.set_facecolor(SURFACE)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.set_axis_off()
    geom = {"left": 3.4, "right": 6.6}
    ax.plot([5, 5], [9.3, 7.4], color=AXIS, linewidth=3, solid_capstyle="round")
    ax.plot([geom["left"], geom["right"]], [7.4, 7.4], color=AXIS, linewidth=3,
            solid_capstyle="round")
    ax.annotate("pelvis / trunk\n(not instrumented)", xy=(5, 9.5), ha="center",
                va="bottom", fontsize=7.5, color=MUTED)
    for x in geom.values():
        ax.plot([x, x], [7.4, 0.9], color=AXIS, linewidth=3, zorder=1,
                solid_capstyle="round")
    unmapped = []
    for key in keys:
        info = SENSOR_MAP.get(key)
        if not info or info.get("side") not in geom:
            unmapped.append(key)
            continue
        x = geom[info["side"]]
        y = 5.6 if info["segment"] == "femoral" else 2.6
        ax.scatter([x], [y], s=340, color=colors[key], zorder=3,
                   edgecolor=SURFACE, linewidth=2)
        side = -1 if info["side"] == "left" else 1
        ax.annotate(f"{fmt_key(key)}\n{info['label']}",
                    xy=(x + side * 0.55, y), ha="left" if side > 0 else "right",
                    va="center", fontsize=7.5, color=INK)
    ax.annotate("knee", xy=(geom["left"] - 0.35, 4.1), ha="right", va="center",
                fontsize=7.5, color=MUTED)
    ax.annotate("knee", xy=(geom["right"] + 0.35, 4.1), ha="left", va="center",
                fontsize=7.5, color=MUTED)
    ax.scatter([geom["left"], geom["right"]], [4.1, 4.1], s=55, facecolor=SURFACE,
               edgecolor=AXIS, linewidth=1.8, zorder=2)
    if unmapped:
        # not placed yet: park them beside the figure so the reader sees them
        for i, key in enumerate(unmapped):
            y = 1.6 + i * 1.0
            ax.scatter([9.2], [y], s=200, color=colors[key], zorder=3,
                       edgecolor=SURFACE, linewidth=2, clip_on=False)
            ax.annotate(f"{fmt_key(key)}", xy=(9.2, y + 0.55), ha="center",
                        va="bottom", fontsize=7.5, color=INK)
        ax.annotate("placement not set:\nrun --plot tap on a\ncalibration recording\n"
                    "and record it in\nSENSOR_MAP or --map",
                    xy=(9.2, 1.6 + len(unmapped) * 1.0 + 0.4), ha="center",
                    va="bottom", fontsize=6.8, color=MUTED, linespacing=1.3)
    _titles(ax, "Sensor placement",
            "thigh + shank on each instrumented side: knee angle is observable, hip "
            "and ankle are not", wrap=54)


def _page_sampling(plt, fig, signals, keys, colors, gap_us):
    from matplotlib.colors import LinearSegmentedColormap
    blues = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
    cmap = LinearSegmentedColormap.from_list("coverage", blues)
    gap_ms = gap_us / 1000

    # --- instantaneous sampling frequency ---------------------------------
    ax = _cell(fig, 0, 0)
    bins = np.logspace(np.log10(5), np.log10(50_000), 190)
    for key in keys:
        dt = signals[key]["dt"]
        ax.hist(1e6 / dt[dt > 0], bins=bins, histtype="step", linewidth=1.8,
                color=colors[key])
    nominal = float(np.median([1e6 / signals[k]["dt_median"] for k in keys]))
    share = float(np.mean([signals[k]["frac_at_nominal"] for k in keys])) * 100
    ax.axvline(nominal, color=INK_2, linewidth=1.2, linestyle="--", zorder=1)
    ax.annotate(f"nominal\n{nominal:,.0f} Hz", xy=(nominal, 0.97),
                xycoords=("data", "axes fraction"), xytext=(-7, 0),
                textcoords="offset points", ha="right", va="top",
                fontsize=9, fontweight="bold", color=INK)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("instantaneous sampling frequency, 1/dt  (Hz)", fontsize=9, color=INK_2)
    ax.set_ylabel("number of intervals", fontsize=9, color=INK_2)
    _titles(ax, "Sampling-frequency distribution",
            f"{share:.0f}% of intervals fall within +/-20% of {nominal:,.0f} Hz - the "
            f"bulk of the record really is sampled at the full rate", wrap=62)
    _style(ax)

    # --- nominal vs delivered ---------------------------------------------
    ax = _cell(fig, 1, 0)
    xs = np.arange(len(keys))
    nom = [1e6 / signals[k]["dt_median"] for k in keys]
    dlv = [len(signals[k]["t"]) / signals[k]["duration_s"] for k in keys]
    b1 = ax.bar(xs - 0.2, nom, 0.38, color=[colors[k] for k in keys], alpha=0.42)
    b2 = ax.bar(xs + 0.2, dlv, 0.38, color=[colors[k] for k in keys])
    _bar_labels(ax, b1, nom, "{:,.0f}")
    _bar_labels(ax, b2, dlv, "{:,.0f}")
    ax.set_xticks(xs, [f"{fmt_key(k)}\n{seg_short(k)}" for k in keys], fontsize=8)
    ax.set_ylim(0, max(nom) * 1.22)
    ax.set_ylabel("rate (Hz)", fontsize=9, color=INK_2)
    _titles(ax, "Nominal vs delivered rate",
            "pale = nominal conversion rate, solid = delivered; the shortfall is "
            "lost blocks or FIFO overflow, not sensor dropout", wrap=62)
    _style(ax, grid_axis="y")

    # --- coverage over time -----------------------------------------------
    ax = _cell(fig, 0, 1)
    t0 = min(s["t"][0] for s in signals.values()) / 1e6
    t1 = max(s["t"][-1] for s in signals.values()) / 1e6
    nbin = 240
    edges = np.linspace(t0, t1, nbin + 1)
    grid = np.zeros((len(keys), nbin))
    for i, key in enumerate(keys):
        s = signals[key]
        tt = s["t"][:-1] / 1e6
        idx = np.clip(np.searchsorted(edges, tt, "right") - 1, 0, nbin - 1)
        covered = np.zeros(nbin)
        np.add.at(covered, idx, np.where(s["dt"] <= gap_us, s["dt"], 0) / 1e6)
        grid[i] = covered / ((t1 - t0) / nbin)
    im = ax.imshow(np.clip(grid, 0, 1), aspect="auto", cmap=cmap, vmin=0, vmax=1,
                   extent=[t0, t1, len(keys) - 0.5, -0.5], interpolation="nearest")
    ax.set_yticks(range(len(keys)), [f"{fmt_key(k)} {seg_short(k)}" for k in keys],
                  fontsize=8)
    ax.set_xlabel("time (s)", fontsize=9, color=INK_2)
    cb = fig.colorbar(im, ax=ax, pad=0.015, fraction=0.04)
    cb.outline.set_visible(False)
    cb.ax.tick_params(colors=MUTED, labelsize=7.5)
    _titles(ax, "Data coverage over time",
            "fraction of each time bin sampled at the nominal rate; pale bands "
            "are dropout", wrap=62)
    _style(ax, grid_axis=None)
    ax.tick_params(colors=MUTED, labelsize=8)

    # --- gap budget --------------------------------------------------------
    ax = _cell(fig, 1, 1)
    lost = [signals[k]["gap_time_s"] for k in keys]
    kept = [signals[k]["duration_s"] - l for k, l in zip(keys, lost)]
    ypos = np.arange(len(keys))[::-1]
    ax.barh(ypos, kept, height=0.6, color=[colors[k] for k in keys], label="sampled")
    ax.barh(ypos, lost, height=0.6, left=kept, color="#dcdbd4")
    for y, k, kp, ls in zip(ypos, keys, kept, lost):
        ax.annotate(f"{kp / (kp + ls) * 100:.0f}% sampled", xy=(kp / 2, y),
                    ha="center", va="center", fontsize=8, color="#ffffff",
                    fontweight="bold")
        ax.annotate(f"{ls:.1f} s lost in {signals[k]['n_gaps']:,} gaps",
                    xy=(kp + ls, y), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=8, color=INK_2)
    ax.set_yticks(ypos, [f"{fmt_key(k)} {seg_short(k)}" for k in keys], fontsize=8)
    ax.set_xlim(0, max(k + l for k, l in zip(kept, lost)) * 1.42)
    ax.set_xlabel("session time (s)", fontsize=9, color=INK_2)
    _titles(ax, "Where the session time went",
            f"grey is time inside gaps longer than {gap_ms:g} ms - resample against "
            "t_us and carry a validity mask", wrap=66)
    _style(ax, grid_axis="x")


def _page_noise(plt, fig, signals, noise, psds, meta, keys, colors, f_cut, fs_psd):
    axis_names = ["x", "y", "z"]

    def bars(ax, values, ylabel, title, subtitle, fmt="{:.2f}"):
        width = 0.8 / len(keys)
        peak = 0.0
        for i, key in enumerate(keys):
            v = values[key]
            xs = np.arange(3) + (i - (len(keys) - 1) / 2) * width
            b = ax.bar(xs, v, width * 0.86, color=colors[key])
            _bar_labels(ax, b, v, fmt)
            peak = max(peak, float(np.nanmax(v)))
        ax.set_xticks(range(3), [f"{a}-axis" for a in axis_names], fontsize=8.5)
        ax.set_ylim(0, peak * 1.40)
        ax.set_ylabel(ylabel, fontsize=9, color=INK_2)
        _titles(ax, title, subtitle, wrap=66)
        _style(ax, grid_axis="y")

    bars(_cell(fig, 0, 0),
         {k: noise[k]["accel_g"] / signals[k]["baseline"] * 1000.0 for k in keys},
         "noise (mg RMS)", "Accelerometer noise floor",
         f"RMS above {f_cut:g} Hz - clear of the band human movement occupies, so "
         f"this is instrumentation noise whatever the subject did")

    bars(_cell(fig, 1, 0),
         {k: noise[k]["gyro_dps"] * 1000.0 for k in keys},
         "noise (mdeg/s RMS)", "Gyroscope noise floor",
         f"same estimator; one count is {meta['gyro_lsb_dps'] * 1000:.0f} mdeg/s at "
         f"this file's +/-{meta['gyro_fs_dps']:g} deg/s full-scale range", fmt="{:.0f}")

    for col, (name, unit, scale) in enumerate(
            (("accel", "mg/sqrt(Hz)", 1e-3), ("gyro", "(deg/s)/sqrt(Hz)", 1.0))):
        ax = _cell(fig, col, 1)
        for key in keys:
            entry = psds.get(key, {})
            if name not in entry:
                continue
            f, p = entry[name]
            m = f > 0
            ax.loglog(f[m], np.sqrt(p[m]) / scale, color=colors[key], linewidth=1.4)
        ax.axvspan(f_cut, fs_psd / 2, color=MUTED, alpha=0.10, zorder=0, linewidth=0)
        ax.axvline(f_cut, color=INK_2, linewidth=1.2, linestyle="--", zorder=1)
        ax.annotate(f"{f_cut:g} Hz\nmotion | noise", xy=(f_cut, 0.97),
                    xycoords=("data", "axes fraction"), xytext=(6, 0),
                    textcoords="offset points", ha="left", va="top",
                    fontsize=8.5, fontweight="bold", color=INK)
        ax.set_xlabel("frequency (Hz)", fontsize=9, color=INK_2)
        ax.set_ylabel(f"|{name}| ASD  ({unit})", fontsize=9, color=INK_2)
        _titles(ax, f"Spectral density of |{name}|",
                "where the recorded energy sits: movement below the line, "
                f"noise above it (grid {fs_psd:,.0f} Hz)", wrap=66)
        _style(ax)


def _page_calibration(plt, fig, signals, meta, keys, colors):
    afs, gfs = meta["accel_fs_g"], meta["gyro_fs_dps"]
    lsb_mdps = meta["gyro_lsb_dps"] * 1000.0

    # --- resting tilt -------------------------------------------------------
    ax = _cell(fig, 0, 0)
    tilts, biases, rest, head_a, head_g = [], [], [], [], []
    for key in keys:
        s = signals[key]
        a = np.median(s["accel"], axis=0)
        tilts.append(np.degrees(np.arccos(np.clip(abs(a[2]) / np.linalg.norm(a), -1, 1))))
        biases.append(np.median(s["gyro"], axis=0) * 1000.0 if s["gyro"] is not None
                      else np.full(3, np.nan))
        rest.append(s["baseline"])
        head_a.append(float(np.max(np.abs(s["accel"]))))
        head_g.append(float(np.max(np.abs(s["gyro"]))) if s["gyro"] is not None else 0.0)
    b = ax.bar(range(len(keys)), tilts, 0.55, color=[colors[k] for k in keys])
    _bar_labels(ax, b, tilts, "{:.1f}°")
    ax.set_xticks(range(len(keys)), [f"{fmt_key(k)}\n{seg_short(k)}" for k in keys],
                  fontsize=8)
    ax.set_ylim(0, max(tilts) * 1.35 + 1e-9)
    ax.set_ylabel("angle from vertical (degrees)", fontsize=9, color=INK_2)
    _titles(ax, "Resting tilt of each sensor",
            "angle between the sensor +Z axis and measured gravity - how far each "
            "unit sits from Z-up on the limb", wrap=66)
    _style(ax, grid_axis="y")

    # --- gyro bias ----------------------------------------------------------
    ax = _cell(fig, 1, 0)
    width = 0.8 / len(keys)
    lim = 0.0
    for i, key in enumerate(keys):
        xs = np.arange(3) + (i - (len(keys) - 1) / 2) * width
        bb = ax.bar(xs, biases[i], width * 0.86, color=colors[key])
        _bar_labels(ax, bb, biases[i], "{:.0f}")
        lim = max(lim, float(np.nanmax(np.abs(biases[i]))))
    ax.axhline(0, color=AXIS, linewidth=1.0)
    ax.set_xticks(range(3), ["x-axis", "y-axis", "z-axis"], fontsize=8.5)
    ax.set_ylim(-lim * 1.7 - 1e-9, lim * 1.7 + 1e-9)
    ax.set_ylabel("static bias (mdeg/s)", fontsize=9, color=INK_2)
    _titles(ax, "Gyroscope static bias",
            f"median output while still - subtract before integrating or it becomes "
            f"unbounded drift; quantised to {lsb_mdps:.0f} mdeg/s per count",
            wrap=62)
    _style(ax, grid_axis="y")

    # --- resting |accel| vs 1 g ---------------------------------------------
    ax = _cell(fig, 0, 1)
    b = ax.bar(range(len(keys)), rest, 0.55, color=[colors[k] for k in keys])
    _bar_labels(ax, b, rest, "{:.4f}")
    ax.axhline(1.0, color=INK_2, linewidth=1.3, linestyle="--")
    ax.annotate(f"nominal 1.000 g  (+/-{afs:g} g full scale, "
                f"{meta['accel_lsb_g'] * 1000:.2f} mg per count)",
                xy=(0.985, 1.0), xycoords=("axes fraction", "data"),
                xytext=(0, -5), textcoords="offset points", ha="right", va="top",
                fontsize=8.5, color=INK)
    ax.set_xticks(range(len(keys)), [f"{fmt_key(k)}\n{seg_short(k)}" for k in keys],
                  fontsize=8)
    lo, hi = min(min(rest), 1.0), max(max(rest), 1.0)
    ax.set_ylim(lo * 0.985, hi * 1.012)
    ax.set_ylabel("resting |accel| (g)", fontsize=9, color=INK_2)
    _titles(ax, "Measured sensitivity vs nominal",
            "each unit's resting |accel| should read 1 g; the gap to nominal is "
            "that unit's gain error (plus any motion left in the median)", wrap=66)
    _style(ax, grid_axis="y")

    # --- dynamic range headroom ---------------------------------------------
    ax = _cell(fig, 1, 1)
    ypos = np.arange(len(keys))[::-1]
    used_a = [min(h / afs, 1.0) * 100 for h in head_a]
    used_g = [min(h / gfs, 1.0) * 100 for h in head_g]
    ax.barh(ypos + 0.19, used_a, height=0.34, color=[colors[k] for k in keys])
    ax.barh(ypos - 0.19, used_g, height=0.34, color=[colors[k] for k in keys],
            alpha=0.45)
    for y, ua, ug, ha_, hg in zip(ypos, used_a, used_g, head_a, head_g):
        ax.annotate(f"accel  peak {ha_:.1f} g", xy=(ua, y + 0.19), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=8, color=INK_2)
        ax.annotate(f"gyro  peak {hg:,.0f} deg/s", xy=(ug, y - 0.19), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=8, color=INK_2)
    ax.axvline(100, color="#d03b3b", linewidth=1.3, linestyle="--")
    ax.set_yticks(ypos, [f"{fmt_key(k)} {seg_short(k)}" for k in keys], fontsize=8)
    ax.set_xlim(0, 168)
    ax.set_xlabel("peak reading as % of full scale", fontsize=9, color=INK_2)
    _titles(ax, "Dynamic-range headroom",
            f"solid = accel against +/-{afs:g} g, pale = gyro against "
            f"+/-{gfs:g} deg/s (this file's header); anything at 100% has saturated",
            wrap=66)
    _style(ax, grid_axis="x")


def _description_chunks():
    """The description split into per-page pairs of column blocks."""
    lines = DATA_DESCRIPTION.rstrip("\n").split("\n")
    n_pages = max(1, -(-len(lines) // (DESC_MAX_PER_COL * 2)))
    per_col = -(-len(lines) // (2 * n_pages))
    per_page = per_col * 2
    return per_col, [lines[i:i + per_page] for i in range(0, len(lines), per_page)]


def _pages_description(plt, pdf, page_no, total_pages):
    """The dataset description, flowed into two columns per page."""
    per_col, chunks = _description_chunks()
    x0, x1 = MARGIN_L / PAGE_W, 1 - MARGIN_R / PAGE_W
    gap = 0.035
    cw = (x1 - x0 - gap) / 2
    top, bottom = 0.862, 0.070

    for n, chunk in enumerate(chunks, start=1):
        fig = _page(plt, "Dataset description",
                    "column dictionary, units and the physical meaning of each signal"
                    + (f"   ({n} of {len(chunks)})" if len(chunks) > 1 else ""),
                    page_no, total_pages)
        for col in (0, 1):
            part = chunk[col * per_col:(col + 1) * per_col]
            if not part:
                continue
            ax = fig.add_axes([x0 + col * (cw + gap), bottom, cw, top - bottom])
            ax.set_axis_off()
            ax.text(0, 1, "\n".join(part), family="monospace", fontsize=DESC_PT,
                    va="top", ha="left", color=INK_2, linespacing=1.42,
                    transform=ax.transAxes)
        pdf.savefig(fig, facecolor=SURFACE)
        plt.close(fig)
        page_no += 1
    return page_no


def plot_general_stats(df, counts, skipped, signals, noise, psds, meta, f_cut,
                       out_path, title, fs_psd, gap_us):
    """Multi-page PDF: every statistic as a visual, description at the back."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    keys = sorted(signals, key=sort_key)
    colors = _sensor_colors(keys)
    total_pages = 4 + len(_description_chunks()[1])

    with PdfPages(out_path) as pdf:
        pages = [
            ("Overview", "what is in the file and where each sensor sits on the body",
             lambda f: _page_overview(plt, f, df, counts, signals, meta, keys, colors)),
            ("Sampling and data continuity",
             "how fast each sensor really ran, and how much of the record survived",
             lambda f: _page_sampling(plt, f, signals, keys, colors, gap_us)),
            ("Noise floor",
             f"instrumentation noise, isolated above {f_cut:g} Hz so it does not "
             f"depend on what motion was recorded",
             lambda f: _page_noise(plt, f, signals, noise, psds, meta, keys, colors,
                                   f_cut, fs_psd)),
            ("Calibration and range",
             "resting geometry, gyro bias and how much of the full scale was used",
             lambda f: _page_calibration(plt, f, signals, meta, keys, colors)),
        ]
        page_no = 1
        for heading, caption, build in pages:
            fig = _page(plt, f"{heading}  -  {title}", caption, page_no, total_pages)
            _page_legend(fig, keys, colors)
            build(fig)
            pdf.savefig(fig, facecolor=SURFACE)
            plt.close(fig)
            page_no += 1

        _pages_description(plt, pdf, page_no, total_pages)

        info = pdf.infodict()
        info["Title"] = f"Signal statistics - {title}"
        info["Subject"] = "Knee-sleeve IMU capture: general signal statistics"
        info["Creator"] = "sensor_stats.py"
    return out_path


def _titles(ax, title, subtitle=None, wrap=88):
    """Panel title with an optional wrapped caption between it and the axes."""
    import textwrap
    if not subtitle:
        ax.set_title(title, fontsize=11, fontweight="bold", color=INK,
                     loc="left", pad=8)
        return
    wrapped = textwrap.wrap(subtitle, wrap)
    ax.set_title(title, fontsize=11, fontweight="bold", color=INK, loc="left",
                 pad=10 + len(wrapped) * 11)
    ax.annotate("\n".join(wrapped), xy=(0, 1.0), xycoords="axes fraction",
                xytext=(0, 6), textcoords="offset points", ha="left",
                va="bottom", fontsize=8.5, color=MUTED, linespacing=1.3)


def _style(ax, grid_axis="both"):
    if grid_axis:
        ax.grid(axis=grid_axis, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.title.set_position((0, 1.0))


def _parse_map(entries):
    """--map DEV:SID=SIDE,SEGMENT  ->  SENSOR_MAP entries (tap_order = given order)."""
    for i, e in enumerate(entries or []):
        try:
            ids, place = e.split("=")
            dev, sid = (int(v) for v in ids.split(":"))
            side, seg = (v.strip().lower() for v in place.split(","))
        except ValueError:
            raise SystemExit(f"--map expects DEV:SID=SIDE,SEGMENT, got {e!r}")
        if seg not in SEGMENT_LABEL:
            raise SystemExit(f"--map segment must be femoral or crural, got {seg!r}")
        SENSOR_MAP[(dev, sid)] = {"side": side, "segment": seg, "tap_order": i + 1}
    _complete_map(SENSOR_MAP)


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv", nargs="*", help="CSV files (default: *.csv beside this script)")
    p.add_argument("--map", action="append", metavar="DEV:SID=SIDE,SEGMENT",
                   help="sensor placement, e.g. 7:1=right,femoral (repeatable; the "
                        "order given is the tap order)")
    p.add_argument("--accel-fs-g", type=float, help="override accel full scale (g)")
    p.add_argument("--gyro-fs-dps", type=float, help="override gyro full scale (deg/s)")
    p.add_argument("--odr-hz", type=float, help="override header ODR (Hz)")
    p.add_argument("--raw", action="store_true",
                   help="CSV holds i16 counts (bin2csv --raw) and has no sidecar")
    p.add_argument("--taps-per-sensor", type=int, default=3,
                   help="taps delivered to each sensor in succession (default: 3)")
    p.add_argument("--z-thresh", type=float, default=Z_THRESHOLD,
                   help=f"spike threshold in robust sigma (default: {Z_THRESHOLD})")
    p.add_argument("--refractory-ms", type=float, default=REFRACTORY_US / 1000,
                   help="ring-down window merged into one impact (default: 120)")
    p.add_argument("--crosstalk-ms", type=float, default=CROSSTALK_US / 1000,
                   help="window in which peaks on different sensors are one tap "
                        "(default: 150)")
    p.add_argument("--ts-outlier-ms", type=float, default=TS_OUTLIER_US / 1000,
                   help="timestamp deviation treated as corrupt (default: 1000)")
    p.add_argument("--gap-ms", type=float, default=GAP_US / 1000,
                   help="interval longer than this is a dropout (default: 1)")
    p.add_argument("--plot", choices=("stats", "tap", "both", "none"), default="stats",
                   help="which figure to produce: 'stats' = general signal statistics "
                        "PDF, valid for any recording (default); 'tap' = the tap-order "
                        "calibration PNG, only valid when every sensor was struck a "
                        "known number of times in a known order; 'both'; or 'none'")
    p.add_argument("--noise-above-hz", type=float, default=20.0,
                   help="frequency above which content is treated as noise rather "
                        "than human movement (default: 20)")
    p.add_argument("--psd-fs", type=float, default=None,
                   help="uniform grid rate for the spectral density panel "
                        "(default: the measured nominal rate)")
    p.add_argument("--out", type=Path,
                   help="output path, .pdf for --plot stats or .png for --plot tap "
                        "(single input file, single figure kind)")
    p.add_argument("--no-plot", action="store_true",
                   help="alias for --plot none")
    p.add_argument("--no-describe", action="store_true",
                   help="omit the dataset description from the output")
    p.add_argument("--describe-only", action="store_true",
                   help="print the dataset description and exit")
    a = p.parse_args(argv)
    if a.no_plot:
        a.plot = "none"
    a.refractory_us = a.refractory_ms * 1000
    a.crosstalk_us = a.crosstalk_ms * 1000
    a.ts_outlier_us = a.ts_outlier_ms * 1000
    a.gap_us = a.gap_ms * 1000
    _parse_map(a.map)
    return a


def main(argv):
    args = parse_args(argv)

    if args.describe_only:
        print(DATA_DESCRIPTION)
        return
    if not args.no_describe:
        print(DATA_DESCRIPTION)

    paths = [Path(p) for p in args.csv] or sorted(Path(__file__).parent.glob("*.csv"))
    if not paths:
        raise SystemExit("No CSV files found.")
    if args.out and len(paths) > 1:
        raise SystemExit("--out only works with a single input file.")

    for path in paths:
        if not path.exists():
            print(f"Skipping missing file: {path}")
            continue
        analyse(path, args)


if __name__ == "__main__":
    main(sys.argv[1:])
