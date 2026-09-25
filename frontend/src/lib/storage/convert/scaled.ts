// The CSV text of one i16 count (decision N: byte-exact with bin2csv.py).
// bin2csv.py writes `f"{count * scale:.6f}"` (accel) / `.4f` (gyro), where
// scale is the header's float32 (fs / 32768 exactly, log_format.h). Every
// such product is exact in a double, and Python rounds the exact value
// half-to-even, so the text is a pure function of (count, fs, decimals):
//   num = |count| * fs * 10^d;  q = num div 32768, r = num mod 32768;
//   round half-to-even on r against 32768. All magnitudes stay < 2^53.
// One table per axis type per file (65536 strings) makes row formatting a
// lookup. A header whose scale is NOT fs / 32768 (never seen; a modified
// header) falls back to the exact BigInt formatter on count * scale, which
// is precisely what Python computes.
import type { FileHeader } from '../binFormat'
import { fixedHalfEven, withPoint } from './decimal'
import type { ValueTables } from './types'

/** Format fact (log_format.h): scale = fs / 32768, the i16 count per full scale. */
export const COUNTS_PER_FULL_SCALE = 32768
/** Table index = count + TABLE_OFFSET. */
export const TABLE_OFFSET = 32768
export const TABLE_SIZE = 65536
/** bin2csv.py's column precisions. */
export const ACCEL_DECIMALS = 6
export const GYRO_DECIMALS = 4

/** Python `f"{count * (fs / 32768):.{decimals}f}"` by integer arithmetic. */
export function formatScaled(count: number, fullScale: number, decimals: number): string {
  const num = Math.abs(count) * fullScale * 10 ** decimals
  let q = Math.floor(num / COUNTS_PER_FULL_SCALE)
  const twice = 2 * (num - q * COUNTS_PER_FULL_SCALE)
  if (twice > COUNTS_PER_FULL_SCALE || (twice === COUNTS_PER_FULL_SCALE && q % 2 === 1)) q += 1
  const text = withPoint(String(q), decimals)
  return count < 0 ? `-${text}` : text
}

/** The same text for any header scale (exact decimal expansion of the
 *  double product, which is what Python formats). */
export function formatScaledExact(count: number, scale: number, decimals: number): string {
  return fixedHalfEven(count * scale, decimals)
}

export function textTable(fullScale: number, scale: number, decimals: number): string[] {
  const fast = scale === fullScale / COUNTS_PER_FULL_SCALE
  const out = new Array<string>(TABLE_SIZE)
  for (let i = 0; i < TABLE_SIZE; i++) {
    const count = i - TABLE_OFFSET
    out[i] = fast ? formatScaled(count, fullScale, decimals) : formatScaledExact(count, scale, decimals)
  }
  return out
}

function valueTable(text: string[]): Float64Array {
  const v = new Float64Array(text.length)
  for (let i = 0; i < text.length; i++) v[i] = Number(text[i])
  return v
}

/** Both tables for one file's header. */
export function buildValueTables(
  h: Pick<FileHeader, 'accelFsG' | 'gyroFsDps' | 'accelScale' | 'gyroScale'>,
): ValueTables {
  const accelText = textTable(h.accelFsG, h.accelScale, ACCEL_DECIMALS)
  const gyroText = textTable(h.gyroFsDps, h.gyroScale, GYRO_DECIMALS)
  return { accelText, gyroText, accelValue: valueTable(accelText), gyroValue: valueTable(gyroText) }
}
