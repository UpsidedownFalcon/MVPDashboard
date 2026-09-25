import { describe, expect, it } from 'vitest'
import type { SensorStats, StatsResult } from './stats'
import { renderSummary, type SummaryInput } from './summary'
import type { MetaJson } from './types'

const sp = (n: number) => ' '.repeat(n)

const META: MetaJson = {
  fw: '1.2.0',
  device_id: 1,
  source_id: 0,
  sensor_count: 2,
  odr_hz: 6400,
  accel_fs_g: 32,
  gyro_fs_dps: 4000,
  accel_scale: 0.0009765625,
  gyro_scale: 0.1220703125,
  session_id: 7,
  source_file: 'LOG_0001.BIN',
  units: 'physical',
  blocks_valid: 1234,
  blocks_bad: 2,
  seq_gaps: 1,
  fifo_overflows: 3,
  clean_end: true,
  time_sync: [{ esp_us: 1000, unix_us: 1700000000000000 }],
  rows: { '1': 1000, '2': 1 },
  unused_tail_blocks: 0,
  first_bad: null,
}

/** Round figures whose printed forms follow by hand from the Python format
 *  specs (section 4.7); 1.25 and 550.5 are exact binary ties. */
const S1: SensorStats = {
  sensorId: 1,
  rows: 1000,
  good: 998,
  nBadTs: 2,
  backwards: 4,
  dtMedian: 156,
  atNominal: 0.984,
  durationS: 0.156,
  nGaps: 3,
  gapTimeS: 0.0123,
  maxGapMs: 1.25,
  nClipAccel: 1,
  nClipGyro: 0,
  baseline: 1,
  accelMedian: [-0.5, 0.25, 0.125],
  gyroMedian: [1.1, -0.05, 0.5],
  tilt: 77.5,
  noise: { windowMs: 22, nWindows: 6, accelG: [0.006, 0.005, 0.0065], gyroDps: [0.476, 0.5505, 0.348] },
  nWin: 141,
}

/** One good sample: no interval, no window (NaN paths). */
const S2: SensorStats = {
  sensorId: 2,
  rows: 1,
  good: 1,
  nBadTs: 0,
  backwards: 0,
  dtMedian: Number.NaN,
  atNominal: Number.NaN,
  durationS: 0,
  nGaps: 0,
  gapTimeS: 0,
  maxGapMs: 0,
  nClipAccel: 0,
  nClipGyro: 0,
  baseline: 1.5,
  accelMedian: [0, 0, 1.5],
  gyroMedian: [0, 0, 0],
  tilt: 0,
  noise: { windowMs: 22, nWindows: 0, accelG: [Number.NaN, Number.NaN, Number.NaN], gyroDps: [Number.NaN, Number.NaN, Number.NaN] },
  nWin: 20,
}

const placement = (sid: number) => (sid === 1 ? 'left thigh' : 'shin')

function input(over: Partial<SummaryInput> = {}): SummaryInput {
  return { csvName: 'LOG_0001.csv', meta: META, stats: { sensors: [S1, S2] }, placement, fCutHz: 20, gapUs: 1000, ...over }
}

const BANNER = '='.repeat(72)

const EXPECTED: string[] = [
  BANNER,
  'File: LOG_0001.csv',
  BANNER,
  'File header and decoder verdict',
  '  firmware 1.2.0  device 1  session 7  ODR 6,400 Hz  +/-32 g  +/-4000 deg/s  units: physical',
  '  blocks 1,234 valid / 2 bad CRC, 1 seq gaps, 3 FIFO overflows, CLEAN end',
  '  UTC sync: 2023-11-14 22:13:20Z (1 sync block(s))',
  '',
  'Total rows (data points) in file : 1,001',
  'Data points attributed to sensors: 1,001',
  '',
  '1) Unique sensors: 2',
  '',
  '2/3/4) Per-sensor breakdown',
  `  device_id  sensor_id    data_points   percent  placement${sp(20)}`,
  '  ---------  ---------  -------------  --------  -----------------------------',
  `${sp(10)}1${sp(10)}1${sp(10)}1,000${sp(3)}99.900%  left thigh${sp(19)}`,
  `${sp(10)}1${sp(10)}2${sp(14)}1${sp(4)}0.100%  shin${sp(25)}`,
  '  ---------  ---------  -------------  --------  -----------------------------',
  `${sp(6)}TOTAL${sp(21)}1,001  100.000%`,
  '',
  'Sampling and data continuity',
  '   sensor  placement                        nominal   delivered   at nom.   gaps>1ms     lost    max gap      clipped',
  '  -------  -----------------------------  ---------  ----------  --------  ---------  -------  ---------  -----------',
  `   (1, 1)  left thigh${sp(24)}6410Hz${sp(6)}6397Hz${sp(5)}98.4%${sp(10)}3${sp(5)}0.0s${sp(6)}1.2ms${sp(8)}1a/0g`,
  `   (1, 2)  shin${sp(31)}nanHz${sp(7)}nanHz${sp(6)}nan%${sp(10)}0${sp(5)}0.0s${sp(6)}0.0ms${sp(8)}0a/0g`,
  '',
  "  'nominal' = 1/median(dt), the rate the sensor actually converts at.",
  "  'delivered' = samples/span, degraded by lost blocks and FIFO overflow.",
  "  'at nom.' = share of intervals within +/-20% of nominal, i.e. the fraction",
  '  of the record genuinely sampled at the full rate.',
  '  timestamps out of order: 4 (figures computed in file order)',
  '',
  '8) Motion-agnostic noise and bias',
  'Motion-agnostic noise  (content above 20 Hz; 22 ms detrended windows inside gap-free bursts, median over windows)',
  '   sensor  placement                        accel noise (mg RMS)  gyro noise (mdeg/s RMS)  rest |a|     wins',
  `${sp(48)}x      y       z        x      y       z${sp(19)}`,
  '  -------  -----------------------------  ----------------------  ----------------------  --------  -------',
  `   (1, 1)  left thigh${sp(24)}6.00${sp(3)}5.00${sp(4)}6.50${sp(6)}476${sp(4)}550${sp(5)}348${sp(3)}1.0000g${sp(8)}6`,
  `   (1, 2)  shin${sp(31)}nan${sp(4)}nan${sp(5)}nan${sp(6)}nan${sp(4)}nan${sp(5)}nan${sp(3)}1.5000g${sp(8)}0`,
  '',
  "  Accelerometer noise is divided by each sensor's own resting |accel| (the",
  "  'rest |a|' column), so per-unit gain error is removed. One count is",
  "  0.977 mg and 122 mdeg/s at this file's +/-32 g / +/-4000 deg/s.",
  '',
  'Rest orientation and gyroscope bias',
  '   sensor  placement                                 median accel (g)      gyro bias (mdeg/s)     tilt',
  `${sp(50)}x        y        z        x      y       z${sp(9)}`,
  '  -------  -----------------------------  ---------------------------  ----------------------  -------',
  `   (1, 1)  left thigh${sp(23)}-0.5000${sp(3)}0.2500${sp(3)}0.1250${sp(5)}1100${sp(4)}-50${sp(5)}500${sp(4)}77.5d`,
  `   (1, 2)  shin${sp(30)}0.0000${sp(3)}0.0000${sp(3)}1.5000${sp(8)}0${sp(6)}0${sp(7)}0${sp(5)}0.0d`,
  '',
  "  'tilt' is the angle between the sensor +Z axis and the median gravity",
  '  vector. Gyro medians are the static bias to remove before integration.',
  '',
]

describe('renderSummary', () => {
  it('renders every line of section 4.7 with the Python widths, trailing spaces included', () => {
    const text = renderSummary(input())
    const lines = text.split('\n')
    // The Python ends every section with print(), so the text ends "\n\n".
    expect(lines[lines.length - 1]).toBe('')
    expect(lines.slice(0, -1)).toEqual(EXPECTED)
    expect(text.endsWith('integration.\n\n')).toBe(true)
    expect(text).toBe(`${EXPECTED.join('\n')}\n`)
  })

  it('omits the out-of-order line when no sensor stepped backwards', () => {
    const text = renderSummary(input({ stats: { sensors: [{ ...S1, backwards: 0 }, S2] } }))
    expect(text).not.toContain('timestamps out of order')
    expect(text).toContain("  of the record genuinely sampled at the full rate.\n\n8) Motion-agnostic")
  })

  it('placement wording is the caller\'s: fallback text and sided text alike, 29 wide', () => {
    const unset = renderSummary(input({ placement: () => 'placement not set' }))
    expect(unset).toContain(`   (1, 1)  placement not set${sp(12)}${sp(5)}6410Hz`)
    expect(unset).toContain(`${sp(10)}1          1          1,000   99.900%  placement not set${sp(12)}`)
    const sided = renderSummary(input({ placement: (sid) => (sid === 1 ? 'right thigh' : 'right shin') }))
    expect(sided).toContain(`   (1, 2)  right shin${sp(19)}`)
    expect(sided).not.toContain('placement not set')
    // a placement longer than the column is never truncated
    const long = renderSummary(input({ placement: () => 'x'.repeat(40) }))
    expect(long).toContain(`   (1, 1)  ${'x'.repeat(40)}  `)
  })

  it('takes the gap and f_cut labels from the tunables', () => {
    const text = renderSummary(input({ gapUs: 500, fCutHz: 25, stats: { sensors: [{ ...S1, noise: { ...S1.noise, windowMs: 17.6 } }] } }))
    expect(text).toContain('  gaps>0.5ms  ')
    expect(text).toContain('(content above 25 Hz; 18 ms detrended windows')
  })

  it('reports no sync and a DIRTY end from the meta', () => {
    const text = renderSummary(input({ meta: { ...META, time_sync: [], clean_end: false, blocks_bad: 0 } }))
    expect(text).toContain('  UTC sync: none (unix_us empty)\n')
    expect(text).toContain('  blocks 1,234 valid / 0 bad CRC, 1 seq gaps, 3 FIFO overflows, DIRTY end\n')
    const three = renderSummary(input({ meta: { ...META, time_sync: [{ esp_us: 1, unix_us: 0 }, { esp_us: 2, unix_us: 5 }, { esp_us: 3, unix_us: 9 }] } }))
    expect(three).toContain('  UTC sync: 1970-01-01 00:00:00Z (3 sync block(s))\n')
  })

  it('prints the footnote lsb for other full scales', () => {
    const text = renderSummary(input({ meta: { ...META, accel_fs_g: 16, gyro_fs_dps: 2000 } }))
    expect(text).toContain("  0.488 mg and 61 mdeg/s at this file's +/-16 g / +/-2000 deg/s.")
    expect(text).toContain('ODR 6,400 Hz  +/-16 g  +/-2000 deg/s  units: physical')
  })

  it('a log without rows renders only the banner, the meta and the zero totals (A2)', () => {
    const text = renderSummary(input({ stats: { sensors: [] } as StatsResult, csvName: 'LOG_0002.csv' }))
    expect(text).toBe(
      [
        BANNER,
        'File: LOG_0002.csv',
        BANNER,
        'File header and decoder verdict',
        '  firmware 1.2.0  device 1  session 7  ODR 6,400 Hz  +/-32 g  +/-4000 deg/s  units: physical',
        '  blocks 1,234 valid / 2 bad CRC, 1 seq gaps, 3 FIFO overflows, CLEAN end',
        '  UTC sync: 2023-11-14 22:13:20Z (1 sync block(s))',
        '',
        'Total rows (data points) in file : 0',
        'Data points attributed to sensors: 0',
        '',
        '1) Unique sensors: 0',
        '',
        '',
      ].join('\n'),
    )
  })

  it('is plain ASCII', () => {
    for (const ch of renderSummary(input())) expect(ch.charCodeAt(0)).toBeLessThan(0x80)
  })
})
