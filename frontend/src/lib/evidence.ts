// Evidence rendering for insight `context` dicts (SPEC §2: an individual flag
// must always show the evidence that fired it).
//
// `z` and `sd` are jargon a trainer should never have to decode, so they are
// translated rather than printed raw.

import { horizonLabel, pct } from './format'

/** Consumed elsewhere on the card — never listed in the expander. */
const HIDDEN_KEYS = new Set(['severity', 'unvalidated'])

const EVIDENCE_LABELS: Record<string, string> = {
  metric: 'metric id',
  metric_name: 'metric',
  window: 'window',
  baseline_window: 'baseline window',
  value: 'value',
  baseline: 'their baseline',
  sd: 'their usual spread',
  z: 'vs their normal range',
  quality: 'link quality',
  baseline_quality: 'baseline link quality',
  coverage: 'window coverage',
  horizon: 'horizon',
  projected: 'projected',
  settles_at: 'settles at',
  threshold: 'threshold',
  composite_avg: 'risk average',
  pred: 'projected',
  trend: 'trend',
  ratio: 'change',
  also: 'also moved',
}

/** Preferred display order; unknown keys follow, in server order. */
const EVIDENCE_ORDER = [
  'metric_name', 'window', 'value', 'baseline', 'baseline_window', 'z',
  'threshold', 'horizon', 'projected', 'settles_at', 'coverage', 'quality',
  'baseline_quality', 'trend', 'also', 'sd', 'metric',
]

export function evidenceLabel(key: string): string {
  return EVIDENCE_LABELS[key] ?? key.replace(/_/g, ' ')
}

export function formatEvidence(key: string, v: unknown): string {
  if (v == null) return '—'
  switch (key) {
    case 'z': {
      const z = Number(v)
      return `${z >= 0 ? '+' : '−'}${Math.abs(z).toFixed(1)}× their usual spread`
    }
    case 'quality':
    case 'baseline_quality':
    case 'coverage':
      return pct(Number(v))
    case 'ratio':
      return `${Math.round(Number(v) * 100)}% of baseline`
    case 'horizon':
      return horizonLabel(String(v))
    default:
      if (Array.isArray(v)) return v.join(', ')
      return typeof v === 'number' ? v.toFixed(1) : String(v)
  }
}

export function evidenceEntries(
  context: Record<string, unknown> | null | undefined,
): [string, unknown][] {
  if (!context) return []
  const keys = Object.keys(context).filter(
    (k) => !HIDDEN_KEYS.has(k) && context[k] != null,
  )
  keys.sort((a, b) => {
    const ia = EVIDENCE_ORDER.indexOf(a)
    const ib = EVIDENCE_ORDER.indexOf(b)
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)
  })
  return keys.map((k) => [k, context[k]])
}
