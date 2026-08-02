// Metric registry — display names/tooltips fixed by biomech SPEC §9, colors by
// UIUX §8 (validated palette). Text never wears these colors; marks do.

export type MetricId = 'm1' | 'm2' | 'm3' | 'm4' | 'm5' | 'composite'

export interface MetricMeta {
  id: MetricId
  label: string
  short: string
  tooltip: string
  /** CSS variable carrying the series color. */
  cssVar: string
  /** Raw hex (charts that can't read CSS vars, e.g. canvas). */
  color: string
}

export const METRICS: MetricMeta[] = [
  {
    id: 'm1',
    label: 'Impact',
    short: 'IMP',
    tooltip: 'Peak shock magnitude reaching the lower leg',
    cssVar: '--series-m1',
    color: '#3987E5',
  },
  {
    id: 'm2',
    label: 'Loading Rate',
    short: 'RATE',
    tooltip: 'How abruptly load is being applied',
    cssVar: '--series-m2',
    color: '#D95926',
  },
  {
    id: 'm3',
    label: 'Accumulated Load',
    short: 'LOAD',
    tooltip: 'Total mechanical work absorbed this session',
    cssVar: '--series-m3',
    color: '#199E70',
  },
  {
    id: 'm4',
    label: 'Movement Control',
    short: 'CTRL',
    tooltip: 'Shock absorption vs. this athlete when fresh',
    cssVar: '--series-m4',
    color: '#C98500',
  },
  {
    id: 'm5',
    label: 'L/R Balance',
    short: 'BAL',
    tooltip: 'Left/right load imbalance',
    cssVar: '--series-m5',
    color: '#D55181',
  },
]

export const COMPOSITE: MetricMeta = {
  id: 'composite',
  label: 'Injury Risk',
  short: 'RISK',
  tooltip: 'Load applied vs. current capacity — a monitoring aid, not a prediction',
  cssVar: '--composite',
  color: '#21F3FC',
}

/** Display bands (SPEC §9 — display only). The band word always accompanies
 *  the color so meaning never rides on color alone. */
export type RiskBand = 'low' | 'moderate' | 'elevated' | 'high'

export function riskBand(value: number): RiskBand {
  if (value < 30) return 'low'
  if (value < 60) return 'moderate'
  if (value < 80) return 'elevated'
  return 'high'
}

export const RISK_BAND_META: Record<RiskBand, { label: string; cssVar: string; color: string }> = {
  low: { label: 'low', cssVar: '--status-good', color: '#0CA30C' },
  moderate: { label: 'moderate', cssVar: '--ink-2', color: '#C3C2B7' },
  elevated: { label: 'elevated', cssVar: '--status-warning', color: '#FAB219' },
  high: { label: 'high', cssVar: '--status-critical', color: '#D03B3B' },
}

/** Biomech flags (UIUX §6): weight drives chip styling; the two "no data" vs
 *  "data coming" states must never look alike. */
export type FlagWeight = 'alert' | 'warning' | 'info' | 'muted'

export const FLAG_META: Record<string, { weight: FlagWeight; label: string; hint: string }> = {
  cal_failed: {
    weight: 'alert',
    label: 'calibration failed',
    hint: 'A sensor is motionless but disagrees with gravity — hardware fault',
  },
  degraded_sensors: {
    weight: 'alert',
    label: 'sensors missing',
    hint: 'Fewer sensors than mapped — the affected metric is not coming',
  },
  saturated: {
    weight: 'alert',
    label: 'saturated',
    hint: 'Sensor range clipped; Impact and Loading Rate are suppressed',
  },
  uncalibrated: {
    weight: 'warning',
    label: 'uncalibrated',
    hint: 'Running on defaults; Movement Control and L/R Balance may be biased',
  },
  partial: {
    weight: 'warning',
    label: 'partial data',
    hint: 'A required sensor went inactive mid-session',
  },
  no_shank: {
    weight: 'warning',
    label: 'no shank',
    hint: 'Impact is falling back to all limbs',
  },
  carried_over: {
    weight: 'info',
    label: 'carried calibration',
    hint: 'Calibrated from a previous session, not measured today',
  },
  warming_up: {
    weight: 'muted',
    label: 'warming up',
    hint: 'Movement Control / L-R Balance need ~1 min of movement — a value is coming',
  },
  unvalidated: {
    weight: 'muted',
    label: 'unvalidated',
    hint: 'Movement Control / L-R Balance have no real-data validation yet',
  },
}

export type Severity = 'info' | 'warning' | 'alert'

export const SEVERITY_RANK: Record<Severity, number> = { info: 1, warning: 2, alert: 3 }

/** Quality meter bands (UIUX §6). */
export function qualityBand(q: number): 'good' | 'warning' | 'critical' {
  if (q >= 0.9) return 'good'
  if (q >= 0.6) return 'warning'
  return 'critical'
}
