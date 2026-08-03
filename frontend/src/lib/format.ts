// Small formatting helpers. Window/horizon labels come from config strings the
// API returns ("5m", "1h") — we prettify, never hardcode durations (UIUX §13).

export function horizonLabel(horizon: string): string {
  return `+${horizon}`
}

export function windowLabel(window: string): string {
  return `past ${window}`
}

export function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000)
  if (s < 60) return `${Math.round(s)}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function shortClock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/** 0–100 metric value for display; null-safe. */
export function metricValue(v: number | null | undefined, digits = 0): string {
  return v == null ? '—' : v.toFixed(digits)
}

export function pct(v: number | null | undefined): string {
  return v == null ? '—' : `${Math.round(v * 100)}%`
}

// --- forecast band labelling (REQUIRED — biomech SPEC §2) ---------------------
// Under model_version 'dose-scenario-1' the forecast's ci_low/ci_high are NOT a
// confidence interval. They are two counterfactuals (docs/ANALYTICS.md §2):
//     ci_low  = "if they stop now"   -> the decaying accumulated-dose floor
//     ci_high = "if load returns to this session's hardest"
// Labelling them "CI" tells a trainer there is a 95% probability the truth lies
// between them — an injury-adjacent probabilistic claim that SPEC §2 forbids
// ("a monitoring and triage aid, not a prediction"). The previous crude UI did
// exactly that. Always render the band through these helpers, never with a
// hardcoded label, because the meaning follows model_version: rows written by
// the older 'linreg-stub-1' really are a statistical prediction interval.

export function isScenarioBand(modelVersion: string): boolean {
  return modelVersion.startsWith('dose-scenario')
}

/** Short label shown beside the numbers, e.g. "range 12.4–48.1". */
export function forecastBandLabel(modelVersion: string): string {
  return isScenarioBand(modelVersion) ? 'range' : 'CI'
}

/** One-line explanation of what the band means. Must be shown with the band. */
export function forecastBandNote(modelVersion: string): string {
  return isScenarioBand(modelVersion)
    ? 'Range spans “if they stop now” to “if load returns to this session’s hardest”. ' +
      'Not a probability.'
    : 'Statistical prediction interval — not a probability of injury.'
}

/** Parse a duration label like "10m" / "2h" to seconds (mirror of the backend
 *  duration syntax; used only for sorting/estimates, never for display). */
export function durationToSeconds(label: string): number {
  const m = /^(\d+)([smhdw])$/.exec(label.trim())
  if (!m) return 0
  const n = Number(m[1])
  return n * { s: 1, m: 60, h: 3600, d: 86400, w: 604800 }[m[2] as 's' | 'm' | 'h' | 'd' | 'w']
}

/** Bucket count for /api/metrics/history that divides the window EVENLY.
 *  A count that doesn't divide the window (e.g. 24 into 30m = 75 s spans over
 *  60 s source rows) makes one bucket in five absorb two rows — a repeating
 *  density artifact — and fractional spans make the server's integer
 *  `bucket_s` drift against real bucket starts. Windows >5m aggregate
 *  1-minute rows, so their span must additionally be a whole number of
 *  minutes. */
export function evenBucketCount(windowLabel: string, maxBuckets = 30): number {
  const s = durationToSeconds(windowLabel)
  if (s <= 0) return 24
  const unit = s > 300 ? 60 : 1
  const units = Math.floor(s / unit)
  for (let n = Math.min(maxBuckets, units); n >= 1; n--) {
    if (units % n === 0) return n
  }
  return 1
}
