// Number formatting for the Sleeve storage page. lib/format.ts has nothing
// for byte counts or durations in milliseconds, and the unit words are
// user-facing, so they come from STORAGE_COPY. Decimal units (1 MB = 1e6 B)
// match the "about 1 MB/s" wording and the ETA maths in transfer.ts.
import { fill, STORAGE_COPY } from './copy'

const KB = 1e3
const MB = 1e6
const GB = 1e9
const MS_PER_S = 1000
const S_PER_MIN = 60
const MIN_PER_H = 60

/** "512.0 MB", "1.2 GB", "12.3 KB", "400 B". */
export function formatBytes(n: number): string {
  const u = STORAGE_COPY.units
  if (n >= GB) return `${(n / GB).toFixed(1)} ${u.gb}`
  if (n >= MB) return `${(n / MB).toFixed(1)} ${u.mb}`
  if (n >= KB) return `${(n / KB).toFixed(1)} ${u.kb}`
  return `${Math.round(n)} ${u.b}`
}

/** "1.0 MB/s". */
export function formatRate(bytesPerS: number): string {
  return fill(STORAGE_COPY.units.perSecond, { n: (bytesPerS / MB).toFixed(1) })
}

/** "45 s", "9 min", "1 h 5 min". Never negative. */
export function formatEta(ms: number): string {
  const u = STORAGE_COPY.units
  const totalS = Math.max(0, Math.ceil(ms / MS_PER_S))
  if (totalS < S_PER_MIN) return fill(u.seconds, { n: totalS })
  const totalMin = Math.ceil(totalS / S_PER_MIN)
  if (totalMin < MIN_PER_H) return fill(u.minutes, { n: totalMin })
  return fill(u.hoursMinutes, { h: Math.floor(totalMin / MIN_PER_H), m: totalMin % MIN_PER_H })
}

/** Whole-percent progress, clamped 0..100; 0 when the total is unknown. */
export function percent(done: number, total: number): number {
  if (total <= 0) return 0
  return Math.min(100, Math.max(0, Math.floor((done / total) * 100)))
}
