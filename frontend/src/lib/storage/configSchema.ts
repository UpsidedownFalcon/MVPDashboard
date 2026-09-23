// CONFIG.TXT vocabulary: the 14 keys in firmware order (config_file.c
// s_keys[]) with the ranges config_load() enforces. Firmware limits are
// on-disk format facts, not tunables, so they live here and not in config.ts.
// Validation mirrors the parser exactly where it matters (UTF-8 byte limits,
// C isspace trimming, the fgets(160) line limit, strtoul base 0) so the UI
// can refuse what the firmware would silently mangle and show what it would
// actually read.
import { ACCEL_FS_ALLOWED_G, GYRO_FS_ALLOWED_DPS } from '../config'

export const CONFIG_KEY_NAMES = [
  'wifi_ssid',
  'wifi_password',
  'udp_ip',
  'udp_port',
  'device_id',
  'source_id',
  'diag_log_enabled',
  'stream_enabled',
  'low_batt_mv',
  'accel_fs_g',
  'gyro_fs_dps',
  'wifi_tx_power_dbm',
  'batt_cal_true_mv',
  'batt_cal_raw_mv',
] as const

export type ConfigKey = (typeof CONFIG_KEY_NAMES)[number]
export type ConfigKind = 'str' | 'int' | 'bool' | 'enum' | 'ip' | 'qdbm'
/** Decision F: which keys the Basic view edits; the rest are Advanced. */
export type ConfigGroup = 'basic' | 'advanced'
/** When an edit takes effect: 'boot' needs a power cycle, 'session' the next
 *  session (the sleeve re-reads the file about 2 s after unplugging). */
export type ConfigEffect = 'boot' | 'session'

export interface ConfigKeySpec {
  readonly key: ConfigKey
  readonly kind: ConfigKind
  readonly min?: number
  readonly max?: number
  readonly allowed?: readonly number[]
  /** UTF-8 byte limit of the value (the firmware's char buffer minus NUL). */
  readonly maxBytes?: number
  readonly group: ConfigGroup
  readonly effect: ConfigEffect
  /** "0 or min..max" ranges (battery calibration). */
  readonly zeroOr?: { readonly min: number; readonly max: number }
  /** An empty value is accepted (the firmware then keeps its compiled-in
   *  default). Only wifi_password; an empty wifi_ssid is blocked (4.2). */
  readonly allowEmpty?: boolean
}

/** config_load(): fgets(line, 160) reads at most this many bytes per call, so
 *  a physical line longer than this is parsed in pieces. */
export const FGETS_BUFFER_BYTES = 160
export const MAX_LINE_BYTES = FGETS_BUFFER_BYTES - 1

/** app_cfg_t buffers: wifi_ssid[33], wifi_password[65], udp_ip[16]. */
export const WIFI_SSID_MAX_BYTES = 32
export const WIFI_PASSWORD_MAX_BYTES = 64
export const UDP_IP_MAX_BYTES = 15
export const UDP_PORT_MIN = 1
export const UDP_PORT_MAX = 65535
export const DEVICE_ID_MIN = 0
export const DEVICE_ID_MAX = 255
export const SOURCE_ID_MIN = 0
export const SOURCE_ID_MAX = 1
export const LOW_BATT_MV_MIN = 2500
export const LOW_BATT_MV_MAX = 4200
export const BATT_CAL_MV_MIN = 2500
export const BATT_CAL_MV_MAX = 4500
/** wifi_tx_power_dbm: parse_tx_power_() accepts 2.0..20.0 dBm and stores
 *  quarter-dB units (lroundf(x * 4)), clamped to 8..84. */
export const TX_POWER_MIN_DBM = 2
export const TX_POWER_MAX_DBM = 20
export const QDBM_PER_DBM = 4
export const TX_POWER_MIN_QDBM = 8
export const TX_POWER_MAX_QDBM = 84
/** write_qdbm_(): whole dB then one of these for the quarter. */
export const QDBM_FRACTION_TEXT = ['', '.25', '.5', '.75'] as const
/** unsigned long is 32-bit on the ESP32: strtoul saturates here. */
export const ULONG_MAX_32 = 0xffffffff
export const IPV4_OCTET_MAX = 255

export const CONFIG_KEYS: readonly ConfigKeySpec[] = [
  { key: 'wifi_ssid', kind: 'str', maxBytes: WIFI_SSID_MAX_BYTES, group: 'basic', effect: 'boot' },
  {
    key: 'wifi_password',
    kind: 'str',
    maxBytes: WIFI_PASSWORD_MAX_BYTES,
    group: 'basic',
    effect: 'boot',
    allowEmpty: true,
  },
  { key: 'udp_ip', kind: 'ip', maxBytes: UDP_IP_MAX_BYTES, group: 'advanced', effect: 'session' },
  { key: 'udp_port', kind: 'int', min: UDP_PORT_MIN, max: UDP_PORT_MAX, group: 'advanced', effect: 'session' },
  { key: 'device_id', kind: 'int', min: DEVICE_ID_MIN, max: DEVICE_ID_MAX, group: 'basic', effect: 'session' },
  { key: 'source_id', kind: 'int', min: SOURCE_ID_MIN, max: SOURCE_ID_MAX, group: 'basic', effect: 'session' },
  { key: 'diag_log_enabled', kind: 'bool', group: 'basic', effect: 'session' },
  { key: 'stream_enabled', kind: 'bool', group: 'basic', effect: 'session' },
  {
    key: 'low_batt_mv',
    kind: 'int',
    min: LOW_BATT_MV_MIN,
    max: LOW_BATT_MV_MAX,
    group: 'advanced',
    effect: 'session',
  },
  { key: 'accel_fs_g', kind: 'enum', allowed: ACCEL_FS_ALLOWED_G, group: 'advanced', effect: 'session' },
  { key: 'gyro_fs_dps', kind: 'enum', allowed: GYRO_FS_ALLOWED_DPS, group: 'advanced', effect: 'session' },
  {
    key: 'wifi_tx_power_dbm',
    kind: 'qdbm',
    min: TX_POWER_MIN_DBM,
    max: TX_POWER_MAX_DBM,
    group: 'advanced',
    effect: 'session',
  },
  {
    key: 'batt_cal_true_mv',
    kind: 'int',
    min: 0,
    max: BATT_CAL_MV_MAX,
    zeroOr: { min: BATT_CAL_MV_MIN, max: BATT_CAL_MV_MAX },
    group: 'advanced',
    effect: 'session',
  },
  {
    key: 'batt_cal_raw_mv',
    kind: 'int',
    min: 0,
    max: BATT_CAL_MV_MAX,
    zeroOr: { min: BATT_CAL_MV_MIN, max: BATT_CAL_MV_MAX },
    group: 'advanced',
    effect: 'session',
  },
]

export const CONFIG_SPEC: Readonly<Record<ConfigKey, ConfigKeySpec>> = Object.fromEntries(
  CONFIG_KEYS.map((s) => [s.key, s]),
) as Record<ConfigKey, ConfigKeySpec>

export function isConfigKey(name: string): name is ConfigKey {
  return Object.prototype.hasOwnProperty.call(CONFIG_SPEC, name)
}

/** C isspace() in the "C" locale: space, \t, \n, \v, \f, \r. Bytes >= 0x80
 *  are never whitespace to the firmware. */
export function isCSpace(code: number): boolean {
  return code === 0x20 || (code >= 0x09 && code <= 0x0d)
}

const encoder = new TextEncoder()

export function utf8ByteLength(text: string): number {
  return encoder.encode(text).length
}

export type ConfigValue = string | number

export type ValidateCode =
  | 'empty'
  | 'range'
  | 'not-allowed'
  | 'too-long'
  | 'whitespace'
  | 'format'
  | 'line-too-long'

export type ValidateResult = { ok: true; value: ConfigValue } | { ok: false; code: ValidateCode }

const DIGITS_RE = /^\d+$/
const DOTTED_QUAD_RE = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/
const DECIMAL_RE = /^\d+(\.\d+)?$/
/** Characters that would end or truncate the line before the firmware saw
 *  the whole value. */
const LINE_BREAKING_RE = /[\r\n\0]/

function fail(code: ValidateCode): ValidateResult {
  return { ok: false, code }
}

function ok(value: ConfigValue): ValidateResult {
  return { ok: true, value }
}

/** Validate user text for `key` as the firmware would read it after
 *  encodeValue(). The returned value is canonical (a number for numeric
 *  kinds, quarter-dB units for qdbm, a canonical dotted quad for ip). */
export function validateValue(key: ConfigKey, text: string): ValidateResult {
  const spec = CONFIG_SPEC[key]
  if (text.length > 0 && (isCSpace(text.charCodeAt(0)) || isCSpace(text.charCodeAt(text.length - 1)))) {
    return fail('whitespace')
  }
  if (LINE_BREAKING_RE.test(text)) return fail('format')
  // Conservative by one byte: 159 bytes still fits one fgets() read, but the
  // limit is stated as ">= 159 rejected" in the plan and nothing legitimate
  // gets near it.
  if (utf8ByteLength(key) + 1 + utf8ByteLength(text) >= MAX_LINE_BYTES) return fail('line-too-long')
  if (text === '') return spec.allowEmpty ? ok('') : fail('empty')

  switch (spec.kind) {
    case 'str': {
      if (spec.maxBytes !== undefined && utf8ByteLength(text) > spec.maxBytes) return fail('too-long')
      return ok(text)
    }
    case 'int': {
      if (!DIGITS_RE.test(text)) return fail('format')
      const v = Number(text)
      if (spec.zeroOr) {
        if (v === 0 || (v >= spec.zeroOr.min && v <= spec.zeroOr.max)) return ok(v)
        return fail('range')
      }
      if ((spec.min !== undefined && v < spec.min) || (spec.max !== undefined && v > spec.max)) {
        return fail('range')
      }
      return ok(v)
    }
    case 'bool': {
      if (!DIGITS_RE.test(text)) return fail('format')
      const v = Number(text)
      return v === 0 || v === 1 ? ok(v) : fail('not-allowed')
    }
    case 'enum': {
      if (!DIGITS_RE.test(text)) return fail('format')
      const v = Number(text)
      return spec.allowed?.includes(v) ? ok(v) : fail('not-allowed')
    }
    case 'ip': {
      const m = DOTTED_QUAD_RE.exec(text)
      if (!m) return fail('format')
      const octets = m.slice(1, 5).map(Number)
      if (octets.some((o) => o > IPV4_OCTET_MAX)) return fail('range')
      const canonical = octets.join('.')
      if (spec.maxBytes !== undefined && canonical.length > spec.maxBytes) return fail('too-long')
      return ok(canonical)
    }
    case 'qdbm': {
      if (!DECIMAL_RE.test(text)) return fail('format')
      const x = Number(text)
      if (x < TX_POWER_MIN_DBM || x > TX_POWER_MAX_DBM) return fail('range')
      return ok(Math.round(x * QDBM_PER_DBM))
    }
  }
}

/** The text written to the file for a validated value: plain decimal without
 *  leading zeros (strtoul base 0 would read them as octal), quarter-dB text
 *  as write_qdbm_() prints it, a canonical dotted quad. */
export function encodeValue(key: ConfigKey, value: ConfigValue): string {
  const spec = CONFIG_SPEC[key]
  switch (spec.kind) {
    case 'str':
      return String(value)
    case 'ip': {
      const m = DOTTED_QUAD_RE.exec(String(value))
      return m ? m.slice(1, 5).map(Number).join('.') : String(value)
    }
    case 'int':
    case 'bool':
    case 'enum':
      return Math.trunc(Number(value)).toString(10)
    case 'qdbm': {
      const q = Math.round(Number(value))
      return `${Math.floor(q / QDBM_PER_DBM)}${QDBM_FRACTION_TEXT[q % QDBM_PER_DBM]}`
    }
  }
}

function digitValue(ch: string): number {
  const c = ch.charCodeAt(0)
  if (c >= 0x30 && c <= 0x39) return c - 0x30
  if (c >= 0x61 && c <= 0x7a) return c - 0x61 + 10
  if (c >= 0x41 && c <= 0x5a) return c - 0x41 + 10
  return -1
}

/** What parse_u32_() would get from `text`: strtoul(val, &end, 0) with the
 *  whole string required to parse. Leading whitespace skipped, optional
 *  '+'/'-' ('-' negates modulo 2^32), "0x" = hex, a leading "0" = octal,
 *  overflow saturates at ULONG_MAX. null when the firmware would reject the
 *  text and keep its default. The result is NOT range-checked. */
export function firmwareReadsInt(text: string): number | null {
  const n = text.length
  let i = 0
  while (i < n && isCSpace(text.charCodeAt(i))) i++
  let negative = false
  if (i < n && (text[i] === '+' || text[i] === '-')) {
    negative = text[i] === '-'
    i++
  }
  let base = 10
  if (i < n && text[i] === '0') {
    const x = i + 1 < n ? text[i + 1] : ''
    if ((x === 'x' || x === 'X') && i + 2 < n && digitValue(text[i + 2]) >= 0 && digitValue(text[i + 2]) < 16) {
      base = 16
      i += 2
    } else {
      base = 8
    }
  }
  const start = i
  let v = 0
  let overflow = false
  while (i < n) {
    const d = digitValue(text[i])
    if (d < 0 || d >= base) break
    v = v * base + d
    if (v > ULONG_MAX_32) overflow = true
    i++
  }
  if (i === start || i !== n) return null
  if (overflow) v = ULONG_MAX_32
  if (negative && v !== 0) v = ULONG_MAX_32 + 1 - v
  return v
}

const STRTOF_DECIMAL_RE = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/

/** What parse_tx_power_() would store (quarter-dB) for `text`, or null when
 *  the firmware would keep its default. Emulates strtof's decimal grammar
 *  (not hex floats or inf/nan, which no sane file carries) with the whole
 *  string required, the 2.0..20.0 range and lroundf(x * 4) clamped 8..84. */
export function firmwareReadsTxPower(text: string): number | null {
  const trimmed = text.replace(/^[ \t\n\v\f\r]+/, '')
  if (!STRTOF_DECIMAL_RE.test(trimmed)) return null
  const x = Math.fround(Number(trimmed))
  if (!(x >= TX_POWER_MIN_DBM && x <= TX_POWER_MAX_DBM)) return null
  const q = Math.round(x * QDBM_PER_DBM)
  return Math.min(TX_POWER_MAX_QDBM, Math.max(TX_POWER_MIN_QDBM, q))
}

/** Mirrors backend/common/kinds.py _UNIT_ID_RE. */
export const UNIT_ID_RE = /^u(\d{1,3})-([01])$/

/** The dashboard's unit id for a sleeve: `u<device_id>-<source_id>`. */
export function unitIdFrom(deviceId: number, sourceId: number): string {
  return `u${deviceId}-${sourceId}`
}
