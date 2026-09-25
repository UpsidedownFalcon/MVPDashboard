import { describe, expect, it } from 'vitest'
import {
  BLOCK_BYTES,
  BLOCK_TYPE_IMU,
  classifyBlock,
  FILE_HEADER_BYTES,
  MAX_SAMPLES,
  parseFileHeader,
  readBlockHeader,
  SAMPLE_AREA_OFFSET,
  SAMPLE_BYTES,
  SAMPLE_OFF_AX,
  SAMPLE_OFF_AY,
  SAMPLE_OFF_AZ,
  SAMPLE_OFF_DT_US,
  SAMPLE_OFF_GX,
  SAMPLE_OFF_GY,
  SAMPLE_OFF_GZ,
  viewOf,
  type FileHeader,
} from '../binFormat'
import { encodeFileHeader } from '../fixtures/binEncode'
import { fixtureBytes, LOG_0010_HEAD64_BIN } from '../fixtures/load'
import { pyF } from './pyFormat'
import { buildValueTables } from './scaled'
import { medianSorted, MIN_WINDOW_SAMPLES, StatsSink, windowSamples, type SensorStats, type StatsOptions, type StatsResult } from './stats'

type Counts = [number, number, number, number, number, number]

interface Row {
  sid: number
  t: number
  c: Counts
}

/** Every sample of every valid IMU block (sample_count <= 290) in file
 *  order, the way convert.ts feeds the sink. */
function decodeRows(bytes: Uint8Array): { header: FileHeader; rows: Row[] } {
  const parsed = parseFileHeader(bytes)
  if (!parsed.ok) throw new Error(`header: ${parsed.code}`)
  const view = viewOf(bytes)
  const rows: Row[] = []
  for (let off = FILE_HEADER_BYTES; off + BLOCK_BYTES <= bytes.length; off += BLOCK_BYTES) {
    if (classifyBlock(bytes.subarray(off, off + BLOCK_BYTES)) !== 'valid') continue
    const bh = readBlockHeader(view, off)
    if (bh.type !== BLOCK_TYPE_IMU || bh.sampleCount > MAX_SAMPLES) continue
    const base = Number(bh.baseTsUs)
    for (let i = 0; i < bh.sampleCount; i++) {
      const p = off + SAMPLE_AREA_OFFSET + i * SAMPLE_BYTES
      rows.push({
        sid: bh.sensorId,
        t: base + view.getUint16(p + SAMPLE_OFF_DT_US, true),
        c: [
          view.getInt16(p + SAMPLE_OFF_AX, true),
          view.getInt16(p + SAMPLE_OFF_AY, true),
          view.getInt16(p + SAMPLE_OFF_AZ, true),
          view.getInt16(p + SAMPLE_OFF_GX, true),
          view.getInt16(p + SAMPLE_OFF_GY, true),
          view.getInt16(p + SAMPLE_OFF_GZ, true),
        ],
      })
    }
  }
  return { header: parsed.header, rows }
}

function runSink(header: FileHeader, rows: Row[], opts: StatsOptions): StatsResult {
  const sink = new StatsSink(opts)
  sink.start(header, buildValueTables(header))
  for (const r of rows) sink.pass1(r.sid, r.t, ...r.c)
  sink.pass1Done()
  for (const r of rows) sink.pass2(r.sid, r.t, ...r.c)
  return sink.finish()
}

const DEFAULTS: StatsOptions = { fCutHz: 20, gapUs: 1000, tsOutlierUs: 1_000_000 }

function header32(): FileHeader {
  const parsed = parseFileHeader(encodeFileHeader({ accelFsG: 32, gyroFsDps: 4000 }))
  if (!parsed.ok) throw new Error(parsed.code)
  return parsed.header
}

function within(x: number, want: number, tol: number): void {
  expect(Math.abs(x - want)).toBeLessThanOrEqual(tol)
}

function closeTriple(got: readonly number[], want: readonly number[], digits: number): void {
  for (let i = 0; i < 3; i++) expect(got[i]).toBeCloseTo(want[i], digits)
}

// ---------------------------------------------------------------------------
// (a) A tiny synthetic stream. Truth from sensor_stats.py's own functions
// (build_signals, hf_noise, np.median) over the CSV-rounded values of the
// same samples, run with `uv run --with matplotlib python` (scratch
// pytrial/synth_truth.py) and pasted below as literals.
// ---------------------------------------------------------------------------

/** 60 samples at 156 us; one 5156 us gap between i=30 and 31; the sample
 *  at i=45 carries a corrupt (+3 s) timestamp; i=10 clips accel x and i=20
 *  clips gyro x. */
function sensor1(): Row[] {
  const rows: Row[] = []
  for (let i = 0; i < 60; i++) {
    let t = 1000 + 156 * i
    if (i >= 31) t += 5000
    if (i === 45) t += 3_000_000
    let ax = 100 + ((i * 7) % 13)
    let gx = 8 + (i % 5)
    if (i === 10) ax = 32767
    if (i === 20) gx = -32768
    rows.push({
      sid: 1,
      t,
      c: [ax, -300 + ((i * 5) % 11), 900 + ((i * 3) % 7), gx, -16 + ((i * 3) % 7), 4 + ((i * 2) % 9)],
    })
  }
  return rows
}

/** 25 samples at 160 us, nothing wrong. */
function sensor2(): Row[] {
  const rows: Row[] = []
  for (let i = 0; i < 25; i++) rows.push({ sid: 2, t: 2000 + 160 * i, c: [5 + (i % 3), 7, 1020 + (i % 2), 0, 1, -1] })
  return rows
}

function interleave(a: Row[], b: Row[], chunk: number): Row[] {
  const out: Row[] = []
  for (let i = 0; i < Math.max(a.length, b.length); i += chunk) {
    out.push(...a.slice(i, i + chunk), ...b.slice(i, i + chunk))
  }
  return out
}

describe('StatsSink on a synthetic stream (Python truth)', () => {
  // f_cut 2000 Hz: window 220 us -> n_win = max(20, round(220 / 156)) = 20
  const opts: StatsOptions = { fCutHz: 2000, gapUs: 1000, tsOutlierUs: 1_000_000 }
  const result = runSink(header32(), interleave(sensor1(), sensor2(), 10), opts)
  const [s1, s2] = result.sensors

  it('reports the sensors sorted by id', () => {
    expect(result.sensors.map((s) => s.sensorId)).toEqual([1, 2])
  })

  it('sensor 1: filter, intervals, gaps, clipping', () => {
    expect(s1).toMatchObject({ rows: 60, good: 59, nBadTs: 1, backwards: 0, nGaps: 1, nClipAccel: 1, nClipGyro: 1 })
    expect(s1.dtMedian).toBe(156)
    expect(s1.atNominal).toBe(56 / 58) // 0.9655172413793104: the gap and the 312 us step over the outlier fall outside
    expect(s1.durationS).toBe(0.014204)
    expect(s1.gapTimeS).toBe(0.005156)
    expect(s1.maxGapMs).toBe(5.156)
    expect(s1.nWin).toBe(20)
  })

  it('sensor 1: medians and tilt over the CSV values', () => {
    expect(s1.baseline).toBeCloseTo(0.9332510861081278, 15)
    expect(s1.accelMedian).toEqual([0.103516, -0.288086, 0.881836])
    expect(s1.gyroMedian).toEqual([1.2207, -1.5869, 0.9766])
    expect(s1.tilt).toBeCloseTo(19.143868360742044, 12)
  })

  it('sensor 1: windows come from gap-free runs [31, 28]; the rejected sample does not split a run', () => {
    // continuous_runs() sees the interval between the outlier's good
    // neighbours (312 us), so the second run keeps its 28 samples and yields
    // a window; resetting on the rejected sample would give runs of 14 and
    // 14 and only one window.
    expect(s1.noise.nWindows).toBe(2)
    expect(s1.noise.windowMs).toBe(0.22)
    closeTriple(s1.noise.accelG, [3.4768649575620425, 0.003143068171408375, 0.001982839516411472], 12)
    closeTriple(s1.noise.gyroDps, [0.16662379063176275, 0.24784501830336114, 0.3155147187100078], 12)
  })

  it('sensor 2: the clean series', () => {
    expect(s2).toMatchObject({ rows: 25, good: 25, nBadTs: 0, backwards: 0, nGaps: 0, nClipAccel: 0, nClipGyro: 0 })
    expect(s2.dtMedian).toBe(160)
    expect(s2.atNominal).toBe(1)
    expect(s2.durationS).toBe(0.00384)
    expect(s2.gapTimeS).toBe(0)
    expect(s2.maxGapMs).toBe(0.16)
    expect(s2.baseline).toBeCloseTo(0.9961409130379095, 15)
    expect(s2.accelMedian).toEqual([0.005859, 0.006836, 0.996094])
    expect(s2.gyroMedian).toEqual([0, 0.1221, -0.1221])
    expect(s2.tilt).toBeCloseTo(0.5178577803129083, 12)
    expect(s2.noise.nWindows).toBe(1)
    closeTriple(s2.noise.accelG, [0.0007851946748217418, 0, 0.00048616195203760173], 12)
    expect(s2.noise.gyroDps).toEqual([0, 0, 0])
  })
})

// ---------------------------------------------------------------------------
// (b) The real 1.1.0 head: sensor_stats.py --no-describe --no-plot on the
// bin2csv.py CSV of LOG_0010.head64.bin (plan section 5, WP-C), plus the
// full-precision figures of the same run (scratch pytrial/head64_truth.py).
// ---------------------------------------------------------------------------

describe('StatsSink on LOG_0010.head64.bin (sensor_stats.py truth)', () => {
  const { header, rows } = decodeRows(fixtureBytes(LOG_0010_HEAD64_BIN))
  const result = runSink(header, rows, DEFAULTS)
  const [s1, s2] = result.sensors

  function printed(s: SensorStats) {
    return {
      nominal: pyF(1e6 / s.dtMedian, 0),
      delivered: pyF(s.good / s.durationS, 0),
      atNom: pyF(s.atNominal * 100, 1),
      lost: pyF(s.gapTimeS, 1),
      maxGap: pyF(s.maxGapMs, 1),
      mg: s.noise.accelG.map((v) => (v / s.baseline) * 1000),
      gy: s.noise.gyroDps.map((v) => v * 1000),
      gbias: s.gyroMedian.map((v) => v * 1000),
    }
  }

  it('sees 9280 rows per sensor, all good, in file order', () => {
    expect(result.sensors.map((s) => s.sensorId)).toEqual([1, 2])
    for (const s of result.sensors) expect(s).toMatchObject({ rows: 9280, good: 9280, nBadTs: 0, backwards: 0 })
  })

  it('sensor 1 prints as sensor_stats does', () => {
    const p = printed(s1)
    expect(p.nominal).toBe('6410')
    expect(p.delivered).toBe('6404')
    expect(p.atNom).toBe('98.4')
    expect(s1.nGaps).toBe(11)
    expect(p.lost).toBe('0.0')
    expect(p.maxGap).toBe('1.2')
    expect([s1.nClipAccel, s1.nClipGyro]).toEqual([0, 0])
    within(p.mg[0], 6.19, 0.01)
    within(p.mg[1], 5.32, 0.01)
    within(p.mg[2], 6.47, 0.01)
    within(p.gy[0], 476, 1)
    within(p.gy[1], 551, 1)
    within(p.gy[2], 348, 1)
    within(s1.baseline, 1.007, 0.00005)
    expect(s1.noise.nWindows).toBe(60)
    within(s1.accelMedian[0], -0.6865, 0.00005)
    within(s1.accelMedian[1], 0.4131, 0.00005)
    within(s1.accelMedian[2], 0.6123, 0.00005)
    within(p.gbias[0], 1099, 0.5)
    within(p.gbias[1], 1099, 0.5)
    within(p.gbias[2], 488, 0.5)
    within(s1.tilt, 52.6, 0.05)
  })

  it('sensor 1 matches the Python at full precision', () => {
    expect(s1.dtMedian).toBe(156)
    expect(s1.atNominal).toBeCloseTo(0.9838344649207889, 15)
    expect(s1.durationS).toBe(1.449166)
    expect(s1.gapTimeS).toBe(0.012137)
    expect(s1.maxGapMs).toBe(1.233)
    expect(s1.baseline).toBeCloseTo(1.007023972719123, 15)
    expect(s1.accelMedian).toEqual([-0.686523, 0.413086, 0.612305])
    expect(s1.gyroMedian).toEqual([1.0986, 1.0986, 0.4883])
    expect(s1.tilt).toBeCloseTo(52.61237346491344, 12)
    expect(s1.nWin).toBe(141)
    closeTriple(s1.noise.accelG, [0.006235186334259862, 0.0053529199381086535, 0.006517329604251159], 12)
    closeTriple(s1.noise.gyroDps, [0.47632708568115373, 0.5510108967514211, 0.3483732211474897], 12)
  })

  it('sensor 2 prints as sensor_stats does', () => {
    const p = printed(s2)
    expect(p.nominal).toBe('6410')
    expect(p.delivered).toBe('6418')
    expect(p.atNom).toBe('90.0')
    expect(s2.nGaps).toBe(50)
    expect(p.lost).toBe('0.1')
    expect(p.maxGap).toBe('1.2')
    expect([s2.nClipAccel, s2.nClipGyro]).toEqual([0, 0])
    within(p.mg[0], 6.42, 0.01)
    within(p.mg[1], 7.47, 0.01)
    within(p.mg[2], 13.04, 0.01)
    within(p.gy[0], 667, 1)
    within(p.gy[1], 430, 1)
    within(p.gy[2], 544, 1)
    within(s2.baseline, 1.0104, 0.00005)
    expect(s2.noise.nWindows).toBe(37)
    within(s2.accelMedian[0], 0.248, 0.00005)
    within(s2.accelMedian[1], 0.8154, 0.00005)
    within(s2.accelMedian[2], 0.541, 0.00005)
    within(p.gbias[0], 854, 0.5)
    within(p.gbias[1], 122, 0.5)
    within(p.gbias[2], 122, 0.5)
    within(s2.tilt, 57.6, 0.05)
  })

  it('sensor 2 matches the Python at full precision', () => {
    expect(s2.dtMedian).toBe(156)
    expect(s2.atNominal).toBeCloseTo(0.899773682508891, 15)
    expect(s2.durationS).toBe(1.445895)
    expect(s2.gapTimeS).toBe(0.056084)
    expect(s2.maxGapMs).toBe(1.244)
    expect(s2.baseline).toBeCloseTo(1.01038267016982, 15)
    expect(s2.accelMedian).toEqual([0.248047, 0.81543, 0.541016])
    expect(s2.gyroMedian).toEqual([0.8545, 0.1221, 0.1221])
    expect(s2.tilt).toBeCloseTo(57.59445161680919, 12)
    closeTriple(s2.noise.accelG, [0.006485693237428157, 0.007544687465050394, 0.01317813375037739], 12)
    closeTriple(s2.noise.gyroDps, [0.6666587727172668, 0.4298801520331569, 0.5438527299438916], 12)
  })
})

// ---------------------------------------------------------------------------
// Edge cases without a Python twin (Python raises or warns there).
// ---------------------------------------------------------------------------

describe('StatsSink edge cases', () => {
  it('windowSamples: max(20, round_half_even(window_us / dt_median)), minimum without a median', () => {
    expect(windowSamples(20, 156)).toBe(141)
    expect(windowSamples(20, 157)).toBe(140)
    expect(windowSamples(2000, 156)).toBe(MIN_WINDOW_SAMPLES)
    expect(windowSamples(20, Number.NaN)).toBe(MIN_WINDOW_SAMPLES)
    expect(windowSamples(20, 0)).toBe(MIN_WINDOW_SAMPLES)
  })

  it('a single good sample: no interval, no window, medians are the sample', () => {
    const [s] = runSink(header32(), [{ sid: 1, t: 10, c: [0, 0, 1024, 8, 0, 0] }], DEFAULTS).sensors
    expect(s).toMatchObject({ rows: 1, good: 1, nGaps: 0, durationS: 0, gapTimeS: 0, maxGapMs: 0, nWin: MIN_WINDOW_SAMPLES })
    expect(s.dtMedian).toBeNaN()
    expect(s.atNominal).toBeNaN()
    expect(s.baseline).toBe(1)
    expect(s.accelMedian).toEqual([0, 0, 1])
    expect(s.gyroMedian).toEqual([0.9766, 0, 0])
    expect(s.tilt).toBe(0)
    expect(s.noise.nWindows).toBe(0)
    expect(s.noise.accelG.every(Number.isNaN)).toBe(true)
  })

  it('every timestamp rejected: counts kept, medians NaN', () => {
    const rows: Row[] = [
      { sid: 1, t: 5, c: [0, 0, 1024, 0, 0, 0] },
      { sid: 1, t: 5_000_000, c: [0, 0, 1024, 0, 0, 0] },
    ]
    const [s] = runSink(header32(), rows, DEFAULTS).sensors
    expect(s).toMatchObject({ rows: 2, good: 0, nBadTs: 2, nGaps: 0, durationS: 0 })
    expect(s.baseline).toBeNaN()
    expect(s.accelMedian.every(Number.isNaN)).toBe(true)
    expect(s.tilt).toBeNaN()
  })

  it('even-count |a| median averages the two middle values, also across coarse bins', () => {
    const at = (az: number, i: number): Row => ({ sid: 1, t: 156 * i, c: [0, 0, az, 0, 0, 0] })
    // |a| = 1.0, 1.005859, 1.015625, 1.025391 (distinct 1e-4 bins)
    const [a] = runSink(header32(), [1050, 1024, 1040, 1030].map(at), DEFAULTS).sensors
    expect(a.baseline).toBe((1.005859 + 1.015625) / 2)
    expect(a.accelMedian[2]).toBe((1.005859 + 1.015625) / 2)
    // the two middle values share a bin (and a value)
    const [b] = runSink(header32(), [1024, 1024, 1024, 1024].map(at), DEFAULTS).sensors
    expect(b.baseline).toBe(1)
    // odd count: the middle value itself
    const [c] = runSink(header32(), [1050, 1024, 1040].map(at), DEFAULTS).sensors
    expect(c.baseline).toBe(1.015625)
  })

  it('an interval of exactly the gap is not a gap; longer is; dt >= 65536 goes to the overflow map', () => {
    const at = (t: number): Row => ({ sid: 1, t, c: [0, 0, 1024, 0, 0, 0] })
    const [a] = runSink(header32(), [0, 1000, 2000].map(at), DEFAULTS).sensors
    expect(a).toMatchObject({ nGaps: 0, maxGapMs: 1, dtMedian: 1000 })
    const [b] = runSink(header32(), [0, 1001, 2002, 102002].map(at), DEFAULTS).sensors
    expect(b).toMatchObject({ nGaps: 3, gapTimeS: 0.102002, maxGapMs: 100, dtMedian: 1001 })
    expect(b.atNominal).toBe(2 / 3)
  })

  it('a backwards step is counted, skipped for the intervals and ends the window run (A3)', () => {
    const at = (t: number, i: number): Row => ({ sid: 1, t, c: [i % 3, 0, 1024, 0, 0, 0] })
    const ts = [0, 156, 312, 468, 100, 256, 412, 568]
    const [s] = runSink(header32(), ts.map(at), { fCutHz: 20000, gapUs: 1000, tsOutlierUs: 1_000_000 }).sensors
    expect(s).toMatchObject({ good: 8, backwards: 1, nGaps: 0, dtMedian: 156 })
    expect(s.durationS).toBe(0.000568)
    expect(s.noise.nWindows).toBe(0) // n_win 20 > 4 samples per run
  })

  it('refuses out-of-order calls', () => {
    const sink = new StatsSink(DEFAULTS)
    expect(() => sink.pass1(1, 0, 0, 0, 0, 0, 0, 0)).toThrow()
    const h = header32()
    sink.start(h, buildValueTables(h))
    sink.pass1(1, 0, 0, 0, 0, 0, 0, 0)
    expect(() => sink.pass2(1, 0, 0, 0, 0, 0, 0, 0)).toThrow()
    sink.pass1Done()
    expect(() => sink.pass2(2, 0, 0, 0, 0, 0, 0, 0)).toThrow()
    expect(new StatsSink(DEFAULTS).finish()).toEqual({ sensors: [] })
  })

  it('medianSorted', () => {
    expect(medianSorted(new Float64Array([]))).toBeNaN()
    expect(medianSorted(new Float64Array([3]))).toBe(3)
    expect(medianSorted(new Float64Array([1, 3]))).toBe(2)
    expect(medianSorted(new Float64Array([1, 2, 3]))).toBe(2)
  })
})
