// Python's `f"{x:.{d}f}"` for a double, exactly: the binary value is expanded
// to its full decimal form with BigInt and rounded half-to-even at d places
// (docs.python.org, Format Specification Mini-Language: "correctly rounded
// ... the rounding mode for float matches that of the round() builtin").
// JavaScript's toFixed breaks exact ties away from zero and prints -0 as 0,
// so it cannot reproduce bin2csv.py or sensor_stats.py output.

const F64 = new DataView(new ArrayBuffer(8))

/** Python `format(x, f".{decimals}f")`, including 'nan', 'inf' and the sign
 *  of -0.0 and of negatives that round to zero ('-0.0000'). */
export function fixedHalfEven(x: number, decimals: number): string {
  if (Number.isNaN(x)) return 'nan'
  if (x === Infinity) return 'inf'
  if (x === -Infinity) return '-inf'
  F64.setFloat64(0, x)
  const hi = F64.getUint32(0)
  const lo = F64.getUint32(4)
  const neg = hi >>> 31 === 1
  const expBits = (hi >>> 20) & 0x7ff
  let mant = (BigInt(hi & 0xfffff) << 32n) | BigInt(lo)
  let exp: number
  if (expBits === 0) {
    exp = -1074 // subnormal (or zero)
  } else {
    mant |= 1n << 52n
    exp = expBits - 1075
  }
  // x = mant * 2^exp. For exp < 0: mant * 2^-k = mant * 5^k / 10^k.
  let digits: bigint
  let scale: number
  if (exp >= 0) {
    digits = mant << BigInt(exp)
    scale = 0
  } else {
    scale = -exp
    digits = mant * 5n ** BigInt(scale)
  }
  const text = roundDecimal(digits, scale, decimals)
  return neg ? `-${text}` : text
}

/** `digits / 10^scale` rounded half-to-even to `decimals` places, as text. */
export function roundDecimal(digits: bigint, scale: number, decimals: number): string {
  let q: bigint
  if (scale <= decimals) {
    q = digits * 10n ** BigInt(decimals - scale)
  } else {
    const div = 10n ** BigInt(scale - decimals)
    q = digits / div
    const twice = (digits % div) * 2n
    if (twice > div || (twice === div && (q & 1n) === 1n)) q += 1n
  }
  return withPoint(q.toString(), decimals)
}

/** Insert the decimal point `decimals` digits from the right of an unsigned
 *  integer's text, padding with leading zeros ('7', 3 -> '0.007'). */
export function withPoint(intText: string, decimals: number): string {
  if (decimals === 0) return intText
  const padded = intText.padStart(decimals + 1, '0')
  return `${padded.slice(0, -decimals)}.${padded.slice(-decimals)}`
}
