// Python format-spec helpers for the summary text. sensor_stats.py prints its
// tables with f-strings (`{x:>7.2f}`, `{n:>9,}`, `{fs:g}`, `{s:<29}`), rounds
// the noise window with round() and stamps the UTC sync with strftime; this
// module gives each of those an exact JavaScript twin on top of decimal.ts's
// fixedHalfEven (CPython rounds the exact binary value half to even; JS
// toFixed / toPrecision break ties away from zero, so they cannot be used).
import { fixedHalfEven } from './decimal'

/** `:g` default precision: six significant digits (Python docs, format
 *  spec mini-language). */
export const G_PRECISION = 6
/** `:g` uses exponent form when the decimal exponent is below this
 *  (Python: "if -4 <= exp < precision, fixed-point"). */
const G_EXP_LOW = -4

/** `f"{x:.{decimals}f}"`. */
export function pyF(x: number, decimals: number): string {
  return fixedHalfEven(x, decimals)
}

/** `f"{s:>{width}}"`: right-align, pad with spaces, never truncate. */
export function rjust(s: string, width: number): string {
  return s.length >= width ? s : ' '.repeat(width - s.length) + s
}

/** `f"{s:<{width}}"`: left-align, pad with spaces, never truncate. */
export function ljust(s: string, width: number): string {
  return s.length >= width ? s : s + ' '.repeat(width - s.length)
}

/** `f"{n:,}"` for an integer ('18,560', '-1,234'). A non-integer is rounded
 *  half to even first (same text as `{n:,.0f}`). */
export function thousands(n: number): string {
  return groupDigits(fixedHalfEven(n, 0))
}

/** `f"{x:,.{decimals}f}"` ('6,400', '1,234.57'; 'nan' stays 'nan'). */
export function pyFThousands(x: number, decimals: number): string {
  const text = fixedHalfEven(x, decimals)
  if (!/^-?\d/.test(text)) return text
  const dot = text.indexOf('.')
  return dot < 0 ? groupDigits(text) : groupDigits(text.slice(0, dot)) + text.slice(dot)
}

/** Insert ',' every three digits from the right of an optionally signed
 *  integer text. */
function groupDigits(intText: string): string {
  const neg = intText.startsWith('-')
  const digits = neg ? intText.slice(1) : intText
  let out = ''
  for (let i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 === 0) out += ','
    out += digits[i]
  }
  return neg ? `-${out}` : out
}

/** Python `round(x)` with no ndigits: half to even on the exact binary
 *  value (`round(2.5) == 2`, `round(141.5) == 142`). The result is an int in
 *  Python, so a zero has no sign; NaN stays NaN. */
export function pyRound(x: number): number {
  const v = Number(fixedHalfEven(x, 0))
  return v === 0 ? 0 : v
}

/** `f"{x:g}"`: six significant digits, trailing zeros (and a bare point)
 *  stripped, exponent form 'd.ddde-05' / 'd.ddde+06' when the exponent of
 *  the ROUNDED value is below -4 or at least 6 ('32', '4000', '0.5',
 *  '1e-05', '1.23457e+06', '0.00195312'). */
export function pyG(x: number): string {
  if (Number.isNaN(x)) return 'nan'
  if (x === Infinity) return 'inf'
  if (x === -Infinity) return '-inf'
  if (x === 0) return Object.is(x, -0) ? '-0' : '0'
  const neg = x < 0
  const { digits, exp } = significant(Math.abs(x), G_PRECISION)
  let body: string
  if (exp < G_EXP_LOW || exp >= G_PRECISION) {
    const rest = stripZeros(digits.slice(1))
    const sign = exp < 0 ? '-' : '+'
    body = `${digits[0]}${rest ? `.${rest}` : ''}e${sign}${String(Math.abs(exp)).padStart(2, '0')}`
  } else if (exp >= 0) {
    const frac = stripZeros(digits.slice(exp + 1))
    body = `${digits.slice(0, exp + 1)}${frac ? `.${frac}` : ''}`
  } else {
    body = `0.${'0'.repeat(-exp - 1)}${stripZeros(digits)}`
  }
  return neg ? `-${body}` : body
}

function stripZeros(s: string): string {
  return s.replace(/0+$/, '')
}

/** `a` (> 0, finite) rounded half to even to `p` significant digits: exactly
 *  `p` digit characters plus the decimal exponent of the first one. Rounding
 *  is done once, at the right position, on the exact binary value: below
 *  10^p through fixedHalfEven, above it by BigInt on the exact integer part
 *  with the exact fraction as the tie breaker (a double at or above 1e6 has
 *  an exactly representable fraction, x - trunc(x)). A carry into a new
 *  digit ('9.999995' -> '10.0000') re-rounds one position up. */
function significant(a: number, p: number): { digits: string; exp: number } {
  let exp = Number(a.toExponential().split('e')[1])
  for (;;) {
    const k = exp - (p - 1)
    let digits: string
    if (k <= 0) {
      digits = fixedHalfEven(a, -k).replace('.', '').replace(/^0+/, '')
    } else {
      const whole = Math.trunc(a)
      const frac = a - whole
      const div = 10n ** BigInt(k)
      const int = BigInt(whole)
      let q = int / div
      const twice = (int % div) * 2n
      if (twice > div || (twice === div && (frac > 0 || (q & 1n) === 1n))) q += 1n
      digits = q.toString()
    }
    if (digits.length === p) return { digits, exp }
    // p + 1 digits: the rounding carried into a new leading digit.
    exp += 1
  }
}

/** `f"{datetime.fromtimestamp(unixUs / 1e6, timezone.utc):%Y-%m-%d %H:%M:%S}Z"`.
 *  Whole seconds; the microsecond part is rounded as CPython rounds it
 *  (math.modf then round(), a carry bumps the second) and then dropped. */
export function utcStamp(unixUs: number): string {
  const u = unixUs / 1e6
  let secs = Math.trunc(u)
  const us = pyRound((u - secs) * 1e6)
  if (us >= 1e6) secs += 1
  else if (us < 0) secs -= 1
  const d = new Date(secs * 1000)
  const pad = (n: number, w: number) => String(n).padStart(w, '0')
  return (
    `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1, 2)}-${pad(d.getUTCDate(), 2)} ` +
    `${pad(d.getUTCHours(), 2)}:${pad(d.getUTCMinutes(), 2)}:${pad(d.getUTCSeconds(), 2)}Z`
  )
}
