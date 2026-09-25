// Per-sensor statistics for the summary: sensor_stats.py's build_signals
// (lines 373-431), hf_noise (652-699) and the medians of bias_lines
// (794-797), computed in two streaming passes over the samples (plan 4.4,
// decision T) instead of over a whole CSV in memory. Every statistic is over
// the CSV's ROUNDED values (tables.accelValue / gyroValue, what pandas reads
// back), never count * scale, so the figures equal the Python's; only the
// floating summation order can differ (assumption A4).
//
// Pass 1 (timestamps + accel counts): the outlier filter (tsFilter.ts), then
// per good sample the interval statistics over an integer dt histogram, the
// min / max timestamp and a coarse |a| histogram. pass1Done() derives what
// pass 2 needs: the median dt (hence the window length) and the |a| median
// bin(s). Pass 2: six count histograms (exact per-axis medians, since
// count -> CSV value is monotonic), clip counts, the exact |a| values of the
// median bin(s) and the detrended noise windows. finish() sorts and reduces.
//
// Assumption A3 (plan section 3): Python stable-sorts the good timestamps
// before diff(); a stream keeps file order. Firmware timestamps are
// non-decreasing per sensor (monotonic clamp), so this only matters on a
// backwards step, which is counted (`backwards`), left out of the interval
// statistics and ends the current noise-window run. A rejected timestamp is
// dropped exactly as Python drops it: continuous_runs() then sees the
// interval between its good neighbours, so a run splits only when that
// interval exceeds the gap. A sensor with fewer than two good samples has no
// median dt (hf_noise raises on round(nan) there); it gets the minimum window
// length and no windows.
import type { FileHeader } from '../binFormat'
import { pyRound } from './pyFormat'
import { COUNTS_PER_FULL_SCALE, TABLE_OFFSET, TABLE_SIZE } from './scaled'
import { TsFilter } from './tsFilter'
import type { SampleSink, ValueTables } from './types'

/** hf_noise line 674: window_us = 0.44e6 / f_cut ("the length over which a
 *  straight-line fit removes essentially everything below f_cut"). */
export const WINDOW_FACTOR_US = 0.44e6
/** hf_noise min_samples (line 652). */
export const MIN_WINDOW_SAMPLES = 20
/** sensor_stats.py RAIL_COUNTS (line 64): the i16 full-scale count. */
export const RAIL_COUNTS = 32767
/** build_signals line 381: at or beyond this share of the rail is clipped. */
export const RAIL_FRACTION = 0.9999
/** build_signals line 405: intervals within this band of the median dt. */
export const NOMINAL_BAND_LOW = 0.8
export const NOMINAL_BAND_HIGH = 1.2
/** Coarse |a| histogram bin width in g (pass 1). Memory only: pass 2 keeps
 *  the exact values of the median bin(s), so the median is exact. */
export const MAG_BIN_G = 1e-4
/** dt (us) histogram: intervals below this go in the typed array, longer
 *  ones in an overflow map. */
const DT_HIST_BINS = 65536
const AXES = 6
/** Samples whose filter decision is pending: at most six between drains. */
const PENDING_CAPACITY = 16
const GROW_INITIAL = 64
const RAD_TO_DEG = 180 / Math.PI

export type Triple = [number, number, number]

export interface NoiseStats {
  /** hf_noise window_ms: 0.44e6 / f_cut / 1e3. */
  windowMs: number
  /** Accel windows (gyro has the same count). */
  nWindows: number
  /** Median over windows of the residual std, per axis, in g (NaN: none). */
  accelG: Triple
  /** Same in deg/s. */
  gyroDps: Triple
}

export interface SensorStats {
  sensorId: number
  /** Samples fed (CSV rows of this sensor). */
  rows: number
  /** Rows kept by the timestamp filter. */
  good: number
  nBadTs: number
  /** Backwards steps in file order (assumption A3). */
  backwards: number
  /** np.median(dt) (NaN: fewer than two good samples). */
  dtMedian: number
  /** Share of intervals within +-20% of dtMedian (NaN: no interval). */
  atNominal: number
  durationS: number
  nGaps: number
  gapTimeS: number
  maxGapMs: number
  nClipAccel: number
  nClipGyro: number
  /** np.median(|a|) over good rows (NaN: none). */
  baseline: number
  accelMedian: Triple
  gyroMedian: Triple
  /** degrees(arccos(|az| / |a|)) over the medians. */
  tilt: number
  noise: NoiseStats
  /** Samples per noise window: max(20, round(window_us / dtMedian)). */
  nWin: number
}

export interface StatsResult {
  /** Sorted by sensor id. */
  sensors: SensorStats[]
}

export interface StatsOptions {
  fCutHz: number
  gapUs: number
  tsOutlierUs: number
}

/** hf_noise line 674. */
export function windowUs(fCutHz: number): number {
  return WINDOW_FACTOR_US / fCutHz
}

/** hf_noise line 678: max(min_samples, int(round(window_us / dt_median))). */
export function windowSamples(fCutHz: number, dtMedian: number): number {
  if (!(dtMedian > 0) || !Number.isFinite(dtMedian)) return MIN_WINDOW_SAMPLES
  return Math.max(MIN_WINDOW_SAMPLES, pyRound(windowUs(fCutHz) / dtMedian))
}

/** Growable Float64Array (doubles when full). */
class Doubles {
  private buf = new Float64Array(GROW_INITIAL)
  length = 0

  push(v: number): void {
    if (this.length === this.buf.length) {
      const bigger = new Float64Array(this.buf.length * 2)
      bigger.set(this.buf)
      this.buf = bigger
    }
    this.buf[this.length++] = v
  }

  sorted(): Float64Array {
    return this.buf.slice(0, this.length).sort()
  }
}

/** FIFO of samples whose filter decision is still pending. */
class Pending {
  readonly t = new Float64Array(PENDING_CAPACITY)
  readonly c = new Int16Array(PENDING_CAPACITY * AXES)
  private head = 0
  private len = 0

  push(t: number, ax: number, ay: number, az: number, gx: number, gy: number, gz: number): void {
    if (this.len === PENDING_CAPACITY) throw new Error('stats: pending samples overflow')
    const i = (this.head + this.len) % PENDING_CAPACITY
    this.t[i] = t
    const b = i * AXES
    this.c[b] = ax
    this.c[b + 1] = ay
    this.c[b + 2] = az
    this.c[b + 3] = gx
    this.c[b + 4] = gy
    this.c[b + 5] = gz
    this.len++
  }

  /** Slot of the oldest sample, which leaves the queue. */
  shift(): number {
    if (this.len === 0) throw new Error('stats: no pending sample')
    const i = this.head
    this.head = (this.head + 1) % PENDING_CAPACITY
    this.len--
    return i
  }
}

class SensorState {
  rows = 0
  good = 0
  nBadTs = 0
  backwards = 0
  minT = Number.NaN
  maxT = Number.NaN
  prevT = Number.NaN
  nDt = 0
  readonly dtHist = new Uint32Array(DT_HIST_BINS)
  readonly dtOver = new Map<number, number>()
  nGaps = 0
  gapTimeUs = 0
  maxDt = 0
  readonly magHist: Uint32Array
  filter: TsFilter
  readonly pending = new Pending()
  // pass1Done
  dtMedian = Number.NaN
  atNominal = Number.NaN
  durationS = 0
  nWin = MIN_WINDOW_SAMPLES
  magBinLo = -1
  magOffLo = 0
  magBinHi = -1
  magOffHi = 0
  // pass 2
  hists: Uint32Array[] = []
  nClipAccel = 0
  nClipGyro = 0
  magLo = new Doubles()
  magHi = new Doubles()
  prevT2 = Number.NaN
  winT = new Float64Array(0)
  winY = new Float64Array(0)
  winN = 0
  resid = new Float64Array(0)
  readonly stdOut = new Float64Array(AXES)
  readonly stds: Doubles[] = []

  constructor(
    readonly id: number,
    tsOutlierUs: number,
    magBins: number,
  ) {
    this.filter = new TsFilter(tsOutlierUs)
    this.magHist = new Uint32Array(magBins)
    for (let i = 0; i < AXES; i++) this.stds.push(new Doubles())
  }

  preparePass2(tsOutlierUs: number): void {
    this.filter = new TsFilter(tsOutlierUs)
    for (let i = 0; i < AXES; i++) this.hists.push(new Uint32Array(TABLE_SIZE))
    if (this.magBinHi === this.magBinLo) this.magHi = this.magLo
    this.winT = new Float64Array(this.nWin)
    this.winY = new Float64Array(AXES * this.nWin)
    this.resid = new Float64Array(this.nWin)
  }
}

export class StatsSink implements SampleSink {
  private readonly sensors = new Map<number, SensorState>()
  private accelValue = new Float64Array(0)
  private gyroValue = new Float64Array(0)
  private aRail = 0
  private gRail = 0
  private magBins = 0
  private phase: 'idle' | 'pass1' | 'pass2' | 'done' = 'idle'

  constructor(private readonly opts: StatsOptions) {}

  start(header: FileHeader, tables: ValueTables): void {
    this.sensors.clear()
    this.accelValue = tables.accelValue
    this.gyroValue = tables.gyroValue
    // load_meta lines 268-271 and build_signals line 381, in that float order.
    this.aRail = RAIL_COUNTS * (header.accelFsG / COUNTS_PER_FULL_SCALE) * RAIL_FRACTION
    this.gRail = RAIL_COUNTS * (header.gyroFsDps / COUNTS_PER_FULL_SCALE) * RAIL_FRACTION
    const maxAbs = Math.max(Math.abs(tables.accelValue[0]), Math.abs(tables.accelValue[TABLE_SIZE - 1]))
    this.magBins = Math.floor(Math.sqrt(3 * maxAbs * maxAbs) / MAG_BIN_G) + 1
    this.phase = 'pass1'
  }

  pass1(sensorId: number, tUs: number, ax: number, ay: number, az: number, gx: number, gy: number, gz: number): void {
    if (this.phase !== 'pass1') throw new Error('stats: pass1() outside pass 1')
    let s = this.sensors.get(sensorId)
    if (!s) {
      s = new SensorState(sensorId, this.opts.tsOutlierUs, this.magBins)
      this.sensors.set(sensorId, s)
    }
    s.rows++
    s.pending.push(tUs, ax, ay, az, gx, gy, gz)
    s.filter.push(tUs)
    while (s.filter.available > 0) this.settle1(s, s.filter.shift())
  }

  pass1Done(): void {
    if (this.phase !== 'pass1') throw new Error('stats: pass1Done() outside pass 1')
    for (const s of this.sensors.values()) {
      s.filter.flush()
      while (s.filter.available > 0) this.settle1(s, s.filter.shift())
      s.dtMedian = s.nDt > 0 ? medianOfBins(dtBins(s.dtHist, s.dtOver), s.nDt) : Number.NaN
      s.atNominal = s.nDt > 0 ? countInBand(dtBins(s.dtHist, s.dtOver), s.dtMedian) / s.nDt : Number.NaN
      s.durationS = s.good > 1 ? (s.maxT - s.minT) / 1e6 : 0
      s.nWin = windowSamples(this.opts.fCutHz, s.dtMedian)
      locateMedianBins(s)
      s.preparePass2(this.opts.tsOutlierUs)
    }
    this.phase = 'pass2'
  }

  pass2(sensorId: number, tUs: number, ax: number, ay: number, az: number, gx: number, gy: number, gz: number): void {
    if (this.phase !== 'pass2') throw new Error('stats: pass2() outside pass 2')
    const s = this.sensors.get(sensorId)
    if (!s) throw new Error(`stats: sensor ${sensorId} was not seen in pass 1`)
    s.pending.push(tUs, ax, ay, az, gx, gy, gz)
    s.filter.push(tUs)
    while (s.filter.available > 0) this.settle2(s, s.filter.shift())
  }

  finish(): StatsResult {
    if (this.phase === 'idle') return { sensors: [] }
    if (this.phase !== 'pass2') throw new Error('stats: finish() outside pass 2')
    const states = [...this.sensors.values()].sort((a, b) => a.id - b.id)
    const sensors = states.map((s) => this.reduce(s))
    this.phase = 'done'
    return { sensors }
  }

  /** Pass 1, one decided sample: interval statistics and the |a| histogram. */
  private settle1(s: SensorState, good: boolean): void {
    const slot = s.pending.shift()
    if (!good) {
      s.nBadTs++
      return
    }
    const t = s.pending.t[slot]
    const b = slot * AXES
    const ax = this.accelValue[s.pending.c[b] + TABLE_OFFSET]
    const ay = this.accelValue[s.pending.c[b + 1] + TABLE_OFFSET]
    const az = this.accelValue[s.pending.c[b + 2] + TABLE_OFFSET]
    s.good++
    if (s.good === 1) {
      s.minT = t
      s.maxT = t
    } else {
      if (t < s.minT) s.minT = t
      if (t > s.maxT) s.maxT = t
      const dt = t - s.prevT
      if (dt < 0) {
        s.backwards++
      } else {
        s.nDt++
        if (dt < DT_HIST_BINS) s.dtHist[dt]++
        else s.dtOver.set(dt, (s.dtOver.get(dt) ?? 0) + 1)
        if (dt > this.opts.gapUs) {
          s.nGaps++
          s.gapTimeUs += dt
        }
        if (dt > s.maxDt) s.maxDt = dt
      }
    }
    s.prevT = t
    const mag = Math.sqrt(ax * ax + ay * ay + az * az)
    s.magHist[Math.floor(mag / MAG_BIN_G)]++
  }

  /** Pass 2, one decided sample: histograms, clipping, median-bin values,
   *  noise windows. */
  private settle2(s: SensorState, good: boolean): void {
    const slot = s.pending.shift()
    if (!good) return
    const t = s.pending.t[slot]
    const b = slot * AXES
    const c = s.pending.c
    const i0 = c[b] + TABLE_OFFSET
    const i1 = c[b + 1] + TABLE_OFFSET
    const i2 = c[b + 2] + TABLE_OFFSET
    const i3 = c[b + 3] + TABLE_OFFSET
    const i4 = c[b + 4] + TABLE_OFFSET
    const i5 = c[b + 5] + TABLE_OFFSET
    s.hists[0][i0]++
    s.hists[1][i1]++
    s.hists[2][i2]++
    s.hists[3][i3]++
    s.hists[4][i4]++
    s.hists[5][i5]++
    const ax = this.accelValue[i0]
    const ay = this.accelValue[i1]
    const az = this.accelValue[i2]
    const gx = this.gyroValue[i3]
    const gy = this.gyroValue[i4]
    const gz = this.gyroValue[i5]
    if (Math.abs(ax) >= this.aRail || Math.abs(ay) >= this.aRail || Math.abs(az) >= this.aRail) s.nClipAccel++
    if (Math.abs(gx) >= this.gRail || Math.abs(gy) >= this.gRail || Math.abs(gz) >= this.gRail) s.nClipGyro++
    const mag = Math.sqrt(ax * ax + ay * ay + az * az)
    const bin = Math.floor(mag / MAG_BIN_G)
    if (bin === s.magBinLo) s.magLo.push(mag)
    else if (bin === s.magBinHi) s.magHi.push(mag)
    // continuous_runs (line 636): a gap ends the run; a backwards step too (A3).
    if (!Number.isNaN(s.prevT2)) {
      const dt = t - s.prevT2
      if (dt > this.opts.gapUs || dt < 0) s.winN = 0
    }
    s.prevT2 = t
    const n = s.nWin
    const k = s.winN
    s.winT[k] = t
    s.winY[k] = ax
    s.winY[n + k] = ay
    s.winY[2 * n + k] = az
    s.winY[3 * n + k] = gx
    s.winY[4 * n + k] = gy
    s.winY[5 * n + k] = gz
    s.winN = k + 1
    if (s.winN === n) {
      windowStds(s.winT, s.winY, n, s.resid, s.stdOut)
      for (let axis = 0; axis < AXES; axis++) s.stds[axis].push(s.stdOut[axis])
      s.winN = 0
    }
  }

  private reduce(s: SensorState): SensorStats {
    s.filter.flush()
    while (s.filter.available > 0) this.settle2(s, s.filter.shift())
    const accelMedian = tripleOf((axis) => medianOfBins(valueBins(s.hists[axis], this.accelValue), s.good))
    const gyroMedian = tripleOf((axis) => medianOfBins(valueBins(s.hists[3 + axis], this.gyroValue), s.good))
    const [a0, a1, a2] = accelMedian
    // bias_lines line 797: degrees(arccos(clip(|a_z| / norm(a), -1, 1))).
    const norm = Math.sqrt(a0 * a0 + a1 * a1 + a2 * a2)
    const tilt = Math.acos(Math.min(1, Math.max(-1, Math.abs(a2) / norm))) * RAD_TO_DEG
    const stds = s.stds.map((d) => medianSorted(d.sorted()))
    return {
      sensorId: s.id,
      rows: s.rows,
      good: s.good,
      nBadTs: s.nBadTs,
      backwards: s.backwards,
      dtMedian: s.dtMedian,
      atNominal: s.atNominal,
      durationS: s.durationS,
      nGaps: s.nGaps,
      gapTimeS: s.gapTimeUs / 1e6,
      maxGapMs: s.maxDt / 1e3,
      nClipAccel: s.nClipAccel,
      nClipGyro: s.nClipGyro,
      baseline: baselineOf(s),
      accelMedian,
      gyroMedian,
      tilt,
      noise: {
        windowMs: windowUs(this.opts.fCutHz) / 1e3,
        nWindows: s.stds[0].length,
        accelG: [stds[0], stds[1], stds[2]],
        gyroDps: [stds[3], stds[4], stds[5]],
      },
      nWin: s.nWin,
    }
  }
}

function tripleOf(f: (axis: number) => number): Triple {
  return [f(0), f(1), f(2)]
}

/** Which coarse |a| bin(s) hold the middle element(s) of the good samples,
 *  and their rank inside the bin (two bins when the count is even and the
 *  two middle values straddle a bin edge). */
function locateMedianBins(s: SensorState): void {
  if (s.good === 0) return
  const lo = Math.floor((s.good - 1) / 2)
  const hi = Math.floor(s.good / 2)
  let cum = 0
  for (let bin = 0; bin < s.magHist.length; bin++) {
    const c = s.magHist[bin]
    if (c === 0) continue
    if (s.magBinLo < 0 && lo < cum + c) {
      s.magBinLo = bin
      s.magOffLo = lo - cum
    }
    if (hi < cum + c) {
      s.magBinHi = bin
      s.magOffHi = hi - cum
      return
    }
    cum += c
  }
}

/** np.median(|a|): the exact values collected for the median bin(s). */
function baselineOf(s: SensorState): number {
  if (s.good === 0) return Number.NaN
  const lo = s.magLo.sorted()
  if (lo.length !== s.magHist[s.magBinLo]) throw new Error('stats: pass 2 did not repeat pass 1')
  const vLo = lo[s.magOffLo]
  if (s.magBinHi === s.magBinLo) {
    return s.magOffHi === s.magOffLo ? vLo : (vLo + lo[s.magOffHi]) / 2
  }
  const hi = s.magHi.sorted()
  if (hi.length !== s.magHist[s.magBinHi]) throw new Error('stats: pass 2 did not repeat pass 1')
  return (vLo + hi[s.magOffHi]) / 2
}

/** Detrended residual std per axis of one full window (_detrended_std,
 *  lines 644-649, with numpy's std: sqrt(mean((r - mean(r))^2))). `y` holds
 *  the six axes back to back, `n` values each. */
function windowStds(t: Float64Array, y: Float64Array, n: number, resid: Float64Array, out: Float64Array): void {
  let tm = 0
  for (let k = 0; k < n; k++) tm += t[k]
  tm /= n
  let sxx = 0
  for (let k = 0; k < n; k++) {
    const x = t[k] - tm
    sxx += x * x
  }
  for (let axis = 0; axis < AXES; axis++) {
    const base = axis * n
    let ym = 0
    for (let k = 0; k < n; k++) ym += y[base + k]
    ym /= n
    let sxy = 0
    for (let k = 0; k < n; k++) sxy += (t[k] - tm) * (y[base + k] - ym)
    const slope = sxy / sxx
    let rm = 0
    for (let k = 0; k < n; k++) {
      const r = y[base + k] - ym - (t[k] - tm) * slope
      resid[k] = r
      rm += r
    }
    rm /= n
    let ss = 0
    for (let k = 0; k < n; k++) {
      const d = resid[k] - rm
      ss += d * d
    }
    out[axis] = Math.sqrt(ss / n)
  }
}

/** (value, count) pairs of the dt histogram in ascending dt. */
function* dtBins(hist: Uint32Array, over: Map<number, number>): Generator<[number, number]> {
  for (let i = 0; i < hist.length; i++) if (hist[i] > 0) yield [i, hist[i]]
  for (const k of [...over.keys()].sort((a, b) => a - b)) yield [k, over.get(k) as number]
}

/** (value, count) pairs of a count histogram mapped through the CSV value
 *  table, ascending because the table is monotonic in the count. */
function* valueBins(hist: Uint32Array, values: Float64Array): Generator<[number, number]> {
  for (let i = 0; i < hist.length; i++) if (hist[i] > 0) yield [values[i], hist[i]]
}

/** numpy median of `total` values given as ascending (value, count) bins:
 *  the middle value, or the mean of the two middle values. */
function medianOfBins(bins: Iterable<[number, number]>, total: number): number {
  if (total === 0) return Number.NaN
  const lo = Math.floor((total - 1) / 2)
  const hi = Math.floor(total / 2)
  let cum = 0
  let vLo = Number.NaN
  let haveLo = false
  for (const [v, c] of bins) {
    if (!haveLo && lo < cum + c) {
      vLo = v
      haveLo = true
    }
    if (hi < cum + c) return lo === hi ? vLo : (vLo + v) / 2
    cum += c
  }
  return Number.NaN
}

/** build_signals line 405: intervals with 0.8 dt_med <= dt <= 1.2 dt_med. */
function countInBand(bins: Iterable<[number, number]>, dtMedian: number): number {
  const low = NOMINAL_BAND_LOW * dtMedian
  const high = NOMINAL_BAND_HIGH * dtMedian
  let n = 0
  for (const [v, c] of bins) if (v >= low && v <= high) n += c
  return n
}

/** numpy median of an ascending array (NaN when empty). */
export function medianSorted(a: Float64Array): number {
  const n = a.length
  if (n === 0) return Number.NaN
  return n % 2 === 1 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2
}
