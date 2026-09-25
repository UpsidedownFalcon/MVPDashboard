// The per-log summary text (plan section 4.7, decision U): sensor_stats.py's
// own lines rendered from a StatsResult with the Python widths, alignments
// and formats: analyse (lines 944-990) for the banner and section order,
// meta_lines (347-366), inventory_lines (310-338), quality_lines (595-623),
// noise_lines (751-779) and bias_lines (782-807); the DATA_DESCRIPTION
// block, the 5/6) tap note and the 9) PDF line are left out. Each Python
// section ends with print(), so every section is followed by a blank line
// and the text ends with one. Placement wording is the caller's (decision
// S): this module never decides what a sensor id means.
import { ljust, pyF, pyFThousands, pyG, rjust, thousands, utcStamp } from './pyFormat'
import { COUNTS_PER_FULL_SCALE } from './scaled'
import type { SensorStats, StatsResult } from './stats'
import type { MetaJson } from './types'

export interface SummaryInput {
  /** The `File:` line: the CSV's file name (decision U). */
  csvName: string
  meta: MetaJson
  stats: StatsResult
  placement: (sensorId: number) => string
  fCutHz: number
  gapUs: number
}

const BANNER = '='.repeat(72)
/** seg_label column: `{placement:<29}`, trailing spaces included. */
const W_PLACEMENT = 29

export function renderSummary(input: SummaryInput): string {
  const { meta, stats } = input
  const lines: string[] = [BANNER, `File: ${input.csvName}`, BANNER, ...metaLines(meta), '']
  const total = stats.sensors.reduce((n, s) => n + s.rows, 0)
  lines.push(`Total rows (data points) in file : ${thousands(total)}`)
  lines.push(`Data points attributed to sensors: ${thousands(total)}`)
  lines.push('')
  lines.push(`1) Unique sensors: ${stats.sensors.length}`)
  if (stats.sensors.length === 0) {
    // Assumption A2: a log without an IMU row gets this short summary.
    lines.push('')
    return `${lines.join('\n')}\n`
  }
  lines.push('', ...breakdownLines(meta, stats, input.placement, total), '')
  lines.push(...qualityLines(meta, stats, input.placement, input.gapUs), '')
  lines.push('8) Motion-agnostic noise and bias')
  lines.push(...noiseLines(meta, stats, input.placement, input.fCutHz), '')
  lines.push(...biasLines(meta, stats, input.placement), '')
  return `${lines.join('\n')}\n`
}

/** fmt_key (line 92). */
function key(meta: MetaJson, s: SensorStats): string {
  return `(${meta.device_id}, ${s.sensorId})`
}

/** meta_lines, lines 347-366 (the sidecar always exists here). */
function metaLines(meta: MetaJson): string[] {
  const lines = ['File header and decoder verdict']
  lines.push(
    `  firmware ${meta.fw}  device ${meta.device_id}  session ${meta.session_id}  ` +
      `ODR ${pyFThousands(meta.odr_hz, 0)} Hz  +/-${pyG(meta.accel_fs_g)} g  +/-${pyG(meta.gyro_fs_dps)} deg/s  ` +
      `units: ${meta.units}`,
  )
  lines.push(
    `  blocks ${thousands(meta.blocks_valid)} valid / ${meta.blocks_bad} bad CRC, ` +
      `${meta.seq_gaps} seq gaps, ${meta.fifo_overflows} FIFO overflows, ` +
      `${meta.clean_end ? 'CLEAN' : 'DIRTY'} end`,
  )
  if (meta.time_sync.length > 0) {
    lines.push(`  UTC sync: ${utcStamp(meta.time_sync[0].unix_us)} (${meta.time_sync.length} sync block(s))`)
  } else {
    lines.push('  UTC sync: none (unix_us empty)')
  }
  return lines
}

/** inventory_lines, lines 324-337 (the 2/3/4 table; the totals are above). */
function breakdownLines(meta: MetaJson, stats: StatsResult, placement: SummaryInput['placement'], counted: number): string[] {
  const dashes = `  ${'-'.repeat(9)}  ${'-'.repeat(9)}  ${'-'.repeat(13)}  ${'-'.repeat(8)}  ${'-'.repeat(29)}`
  const lines = [
    '2/3/4) Per-sensor breakdown',
    `  ${rjust('device_id', 9)}  ${rjust('sensor_id', 9)}  ${rjust('data_points', 13)}  ${rjust('percent', 8)}  ${ljust('placement', W_PLACEMENT)}`,
    dashes,
  ]
  for (const s of stats.sensors) {
    const pct = counted ? (s.rows / counted) * 100 : 0
    lines.push(
      `  ${rjust(String(meta.device_id), 9)}  ${rjust(String(s.sensorId), 9)}  ${rjust(thousands(s.rows), 13)}  ` +
        `${rjust(pyF(pct, 3), 7)}%  ${ljust(placement(s.sensorId), W_PLACEMENT)}`,
    )
  }
  lines.push(dashes)
  lines.push(`  ${rjust('TOTAL', 9)}  ${rjust('', 9)}  ${rjust(thousands(counted), 13)}  ${rjust(pyF(counted ? 100 : 0, 3), 7)}%`)
  return lines
}

/** quality_lines, lines 595-623, plus the assumption A3 line. */
function qualityLines(meta: MetaJson, stats: StatsResult, placement: SummaryInput['placement'], gapUs: number): string[] {
  const gapMs = gapUs / 1000
  const lines = [
    'Sampling and data continuity',
    `  ${rjust('sensor', 7)}  ${ljust('placement', W_PLACEMENT)}  ${rjust('nominal', 9)}  ${rjust('delivered', 10)}` +
      `  ${rjust('at nom.', 8)}  ${rjust(`gaps>${pyG(gapMs)}ms`, 9)}  ${rjust('lost', 7)}  ${rjust('max gap', 9)}  ${rjust('clipped', 11)}`,
    `  ${'-'.repeat(7)}  ${'-'.repeat(29)}  ${'-'.repeat(9)}  ${'-'.repeat(10)}  ${'-'.repeat(8)}  ${'-'.repeat(9)}` +
      `  ${'-'.repeat(7)}  ${'-'.repeat(9)}  ${'-'.repeat(11)}`,
  ]
  let backwards = 0
  for (const s of stats.sensors) {
    const nominal = s.dtMedian > 0 ? 1e6 / s.dtMedian : Number.NaN
    const delivered = s.durationS ? s.good / s.durationS : Number.NaN
    const clipped = `${s.nClipAccel}a/${s.nClipGyro}g`
    lines.push(
      `  ${rjust(key(meta, s), 7)}  ${ljust(placement(s.sensorId), W_PLACEMENT)}  ${rjust(pyF(nominal, 0), 7)}Hz` +
        `  ${rjust(pyF(delivered, 0), 8)}Hz  ${rjust(pyF(s.atNominal * 100, 1), 7)}%` +
        `  ${rjust(thousands(s.nGaps), 9)}  ${rjust(pyF(s.gapTimeS, 1), 6)}s` +
        `  ${rjust(pyF(s.maxGapMs, 1), 7)}ms  ${rjust(clipped, 11)}`,
    )
    backwards += s.backwards
  }
  lines.push(
    '',
    "  'nominal' = 1/median(dt), the rate the sensor actually converts at.",
    "  'delivered' = samples/span, degraded by lost blocks and FIFO overflow.",
    "  'at nom.' = share of intervals within +/-20% of nominal, i.e. the fraction",
    '  of the record genuinely sampled at the full rate.',
  )
  if (backwards > 0) lines.push(`  timestamps out of order: ${backwards} (figures computed in file order)`)
  return lines
}

/** noise_lines, lines 751-779. */
function noiseLines(meta: MetaJson, stats: StatsResult, placement: SummaryInput['placement'], fCutHz: number): string[] {
  const winMs = stats.sensors[0].noise.windowMs
  const lines = [
    `Motion-agnostic noise  (content above ${pyG(fCutHz)} Hz; ` +
      `${pyF(winMs, 0)} ms detrended windows inside gap-free bursts, median over windows)`,
    `  ${rjust('sensor', 7)}  ${ljust('placement', W_PLACEMENT)}  ${rjust('accel noise (mg RMS)', 22)}` +
      `  ${rjust('gyro noise (mdeg/s RMS)', 22)}  ${rjust('rest |a|', 8)}  ${rjust('wins', 7)}`,
    `  ${rjust('', 7)}  ${ljust('', W_PLACEMENT)}  ${rjust('x', 7)}${rjust('y', 7)}${rjust('z', 8)}  ${rjust('x', 7)}${rjust('y', 7)}${rjust('z', 8)}` +
      `  ${rjust('', 8)}  ${rjust('', 7)}`,
    `  ${'-'.repeat(7)}  ${'-'.repeat(29)}  ${'-'.repeat(22)}  ${'-'.repeat(22)}  ${'-'.repeat(8)}  ${'-'.repeat(7)}`,
  ]
  for (const s of stats.sensors) {
    const mg = s.noise.accelG.map((v) => (v / s.baseline) * 1000)
    const gy = s.noise.gyroDps.map((v) => v * 1000)
    lines.push(
      `  ${rjust(key(meta, s), 7)}  ${ljust(placement(s.sensorId), W_PLACEMENT)}` +
        `  ${rjust(pyF(mg[0], 2), 7)}${rjust(pyF(mg[1], 2), 7)}${rjust(pyF(mg[2], 2), 8)}` +
        `  ${rjust(pyF(gy[0], 0), 7)}${rjust(pyF(gy[1], 0), 7)}${rjust(pyF(gy[2], 0), 8)}` +
        `  ${rjust(pyF(s.baseline, 4), 7)}g  ${rjust(thousands(s.noise.nWindows), 7)}`,
    )
  }
  const lsbG = meta.accel_fs_g / COUNTS_PER_FULL_SCALE
  const lsbDps = meta.gyro_fs_dps / COUNTS_PER_FULL_SCALE
  lines.push(
    '',
    "  Accelerometer noise is divided by each sensor's own resting |accel| (the",
    "  'rest |a|' column), so per-unit gain error is removed. One count is",
    `  ${pyF(lsbG * 1000, 3)} mg and ${pyF(lsbDps * 1000, 0)} mdeg/s ` +
      `at this file's +/-${pyG(meta.accel_fs_g)} g / +/-${pyG(meta.gyro_fs_dps)} deg/s.`,
  )
  return lines
}

/** bias_lines, lines 782-807. */
function biasLines(meta: MetaJson, stats: StatsResult, placement: SummaryInput['placement']): string[] {
  const lines = [
    'Rest orientation and gyroscope bias',
    `  ${rjust('sensor', 7)}  ${ljust('placement', W_PLACEMENT)}  ${rjust('median accel (g)', 27)}` +
      `  ${rjust('gyro bias (mdeg/s)', 22)}  ${rjust('tilt', 7)}`,
    `  ${rjust('', 7)}  ${ljust('', W_PLACEMENT)}  ${rjust('x', 9)}${rjust('y', 9)}${rjust('z', 9)}  ${rjust('x', 7)}${rjust('y', 7)}${rjust('z', 8)}` +
      `  ${rjust('', 7)}`,
    `  ${'-'.repeat(7)}  ${'-'.repeat(29)}  ${'-'.repeat(27)}  ${'-'.repeat(22)}  ${'-'.repeat(7)}`,
  ]
  for (const s of stats.sensors) {
    const a = s.accelMedian
    const g = s.gyroMedian.map((v) => v * 1000)
    lines.push(
      `  ${rjust(key(meta, s), 7)}  ${ljust(placement(s.sensorId), W_PLACEMENT)}` +
        `  ${rjust(pyF(a[0], 4), 9)}${rjust(pyF(a[1], 4), 9)}${rjust(pyF(a[2], 4), 9)}` +
        `  ${rjust(pyF(g[0], 0), 7)}${rjust(pyF(g[1], 0), 7)}${rjust(pyF(g[2], 0), 8)}  ${rjust(pyF(s.tilt, 1), 6)}d`,
    )
  }
  lines.push(
    '',
    "  'tilt' is the angle between the sensor +Z axis and the median gravity",
    '  vector. Gyro medians are the static bias to remove before integration.',
  )
  return lines
}
