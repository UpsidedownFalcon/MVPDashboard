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
    // Tremor index since 2026-08-03 (SPEC §5.4) — no longer shock transmission.
    tooltip: 'Movement tremor vs. this athlete when fresh — rises as control degrades',
    cssVar: '--series-m4',
    color: '#C98500',
  },
  {
    id: 'm5',
    label: 'L/R Balance',
    short: 'BAL',
    // SIGNED −100..+100 (SPEC §5.5): magnitude is the value, sign is the side.
    // Deliberately neutral wording — never "weaker", never cross-session.
    tooltip: 'How evenly load is shared left/right in this session — magnitude, with the side carrying more',
    cssVar: '--series-m5',
    color: '#D55181',
  },
]

/** `m5` is the one signed metric (−100 left-dominant … +100 right-dominant).
 *  Everything that ranks, colours or sizes a metric must use the magnitude. */
export function isSignedMetric(id: MetricId): boolean {
  return id === 'm5'
}

/** Severity/display magnitude for any metric value. */
export function metricMagnitude(id: MetricId, v: number | null): number | null {
  if (v == null) return null
  return isSignedMetric(id) ? Math.abs(v) : v
}

/** Side label for a signed m5 reading. Neutral phrasing only (SPEC §5.5):
 *  a within-session readout of which side is carrying more load right now —
 *  never a statement about a leg being weak, and never compared across
 *  sessions (limb dominance does not reproduce, κ = −0.14…0.60). */
export function m5Side(v: number | null): 'left' | 'right' | 'even' | null {
  if (v == null) return null
  if (Math.abs(v) < 1) return 'even'
  return v > 0 ? 'left' : 'right'
}

export function m5SideLabel(v: number | null): string | null {
  const side = m5Side(v)
  if (side == null) return null
  return side === 'even' ? 'even' : `more load ${side}`
}

export const COMPOSITE: MetricMeta = {
  id: 'composite',
  label: 'Injury Risk',
  short: 'RISK',
  tooltip: 'Load applied vs. current capacity',
  cssVar: '--composite',
  color: '#21F3FC',
}

/** Display bands (display only — SPEC §9; UIUX §9 documents these cutoffs).
 *  The band word always accompanies the color so meaning never rides on color
 *  alone.
 *
 *  ⚠️ Re-cut 2026-08-03 for the post-dose-floor composite (SPEC §6.1a). The old
 *  30/60/80 cutoffs were chosen when accumulated dose entered as an additive
 *  floor and everything read 15–18. On the current scale a FRESH athlete
 *  measures: squats 1.3, walk 2.6, jog 6.0, kick 8.4, single-leg landing 17,
 *  jump 21 — and the SAME jump when fatigued reads ~33. Against 30/60/80 that
 *  entire protocol, fatigue included, collapses into "low": the band would
 *  carry no information at all.
 *
 *  The cutoffs below are anchored on those measured landmarks so the bands say
 *  something a trainer can act on:
 *    low       <10   ordinary activity — walking, jogging, light work
 *    moderate  10–25 real athletic loading — landings, jumps, hard efforts
 *    elevated  25–45 the same work costing more than it should (the measured
 *                    fresh-vs-fatigued jump, 21 → 33, crosses HERE — which is
 *                    the whole point of the capacity model)
 *    high      ≥45   beyond anything the worn protocol produced fresh;
 *                    a depleted athlete still under load
 *
 *  These are DISPLAY bands only. The backend's INSIGHT_WARN/ALERT thresholds
 *  (85/92) are a separate, backend-owned calibration. */
export type RiskBand = 'low' | 'moderate' | 'elevated' | 'high'

export const RISK_BAND_CUTOFFS = { moderate: 10, elevated: 25, high: 45 } as const

export function riskBand(value: number): RiskBand {
  if (value < RISK_BAND_CUTOFFS.moderate) return 'low'
  if (value < RISK_BAND_CUTOFFS.elevated) return 'moderate'
  if (value < RISK_BAND_CUTOFFS.high) return 'elevated'
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
    // Since 2026-08-03 m1/m2 are still reported under saturation, as LOWER
    // BOUNDS (BACKEND_SCHEMA §2, SPEC §3.7) — the UI renders them "≥ x".
    hint: 'Sensor range clipped — Impact and Loading Rate are minimums, not exact values',
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
    hint: 'Calibrated from a previous session',
  },
  warming_up: {
    weight: 'muted',
    label: 'warming up',
    // m4 also re-warms whenever a new movement-intensity band appears (SPEC §5.4)
    hint: 'Movement Control / L-R Balance are learning this athlete’s baseline — a value is coming',
  },
}

// Demo posture (2026-08-05): flags the backend still sends but the UI must not
// render. `unvalidated` returns here when real-world validation exists.
export const HIDDEN_FLAGS: ReadonlySet<string> = new Set(['unvalidated'])

export type Severity = 'info' | 'warning' | 'alert'

export const SEVERITY_RANK: Record<Severity, number> = { info: 1, warning: 2, alert: 3 }

/** Quality meter bands (UIUX §6). */
export function qualityBand(q: number): 'good' | 'warning' | 'critical' {
  if (q >= 0.9) return 'good'
  if (q >= 0.6) return 'warning'
  return 'critical'
}
