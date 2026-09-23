import { describe, expect, it } from 'vitest'
import {
  CONFIG_KEY_NAMES,
  CONFIG_KEYS,
  CONFIG_SPEC,
  encodeValue,
  firmwareReadsInt,
  firmwareReadsTxPower,
  isConfigKey,
  UNIT_ID_RE,
  unitIdFrom,
  validateValue,
} from './configSchema'

describe('CONFIG_KEYS', () => {
  it('lists the 14 firmware keys in s_keys[] order with decision-F groups', () => {
    expect(CONFIG_KEYS.map((s) => s.key)).toEqual([
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
    ])
    expect([...CONFIG_KEY_NAMES]).toEqual(CONFIG_KEYS.map((s) => s.key))
    const basic = CONFIG_KEYS.filter((s) => s.group === 'basic').map((s) => s.key)
    expect(basic).toEqual(['wifi_ssid', 'wifi_password', 'device_id', 'source_id', 'diag_log_enabled', 'stream_enabled'])
    const boot = CONFIG_KEYS.filter((s) => s.effect === 'boot').map((s) => s.key)
    expect(boot).toEqual(['wifi_ssid', 'wifi_password'])
    expect(isConfigKey('udp_ip')).toBe(true)
    expect(isConfigKey('UDP_IP')).toBe(false)
    expect(isConfigKey('toString')).toBe(false)
  })

  it('carries the firmware ranges', () => {
    expect(CONFIG_SPEC.udp_port).toMatchObject({ kind: 'int', min: 1, max: 65535 })
    expect(CONFIG_SPEC.device_id).toMatchObject({ kind: 'int', min: 0, max: 255 })
    expect(CONFIG_SPEC.source_id).toMatchObject({ kind: 'int', min: 0, max: 1 })
    expect(CONFIG_SPEC.low_batt_mv).toMatchObject({ kind: 'int', min: 2500, max: 4200 })
    expect(CONFIG_SPEC.accel_fs_g.allowed).toEqual([2, 4, 8, 16, 32])
    expect(CONFIG_SPEC.gyro_fs_dps.allowed).toEqual([125, 250, 500, 1000, 2000, 4000])
    expect(CONFIG_SPEC.wifi_ssid.maxBytes).toBe(32)
    expect(CONFIG_SPEC.wifi_password.maxBytes).toBe(64)
    expect(CONFIG_SPEC.udp_ip.maxBytes).toBe(15)
    expect(CONFIG_SPEC.wifi_tx_power_dbm).toMatchObject({ kind: 'qdbm', min: 2, max: 20 })
    expect(CONFIG_SPEC.batt_cal_true_mv.zeroOr).toEqual({ min: 2500, max: 4500 })
  })
})

describe('validateValue', () => {
  it('counts UTF-8 bytes for the SSID and password limits', () => {
    const cjk = '\u5bb6'.repeat(10) // 30 bytes
    expect(validateValue('wifi_ssid', cjk)).toEqual({ ok: true, value: cjk })
    expect(validateValue('wifi_ssid', '\u5bb6'.repeat(11))).toEqual({ ok: false, code: 'too-long' })
    expect(validateValue('wifi_ssid', 'a'.repeat(32)).ok).toBe(true)
    expect(validateValue('wifi_ssid', 'a'.repeat(33))).toEqual({ ok: false, code: 'too-long' })
    expect(validateValue('wifi_password', 'p'.repeat(64)).ok).toBe(true)
    expect(validateValue('wifi_password', 'p'.repeat(65))).toEqual({ ok: false, code: 'too-long' })
  })

  it('blocks an empty SSID but allows an empty password', () => {
    expect(validateValue('wifi_ssid', '')).toEqual({ ok: false, code: 'empty' })
    expect(validateValue('wifi_password', '')).toEqual({ ok: true, value: '' })
    expect(validateValue('udp_port', '')).toEqual({ ok: false, code: 'empty' })
  })

  it('rejects leading or trailing whitespace and line breaks (the firmware would trim or split)', () => {
    expect(validateValue('wifi_ssid', ' home')).toEqual({ ok: false, code: 'whitespace' })
    expect(validateValue('wifi_ssid', 'home\t')).toEqual({ ok: false, code: 'whitespace' })
    expect(validateValue('wifi_ssid', 'my home').ok).toBe(true)
    expect(validateValue('wifi_password', 'a\nb')).toEqual({ ok: false, code: 'format' })
  })

  it('checks integer ranges and never keeps leading zeros (the octal trap)', () => {
    expect(validateValue('udp_port', '5050')).toEqual({ ok: true, value: 5050 })
    expect(validateValue('udp_port', '0')).toEqual({ ok: false, code: 'range' })
    expect(validateValue('udp_port', '65536')).toEqual({ ok: false, code: 'range' })
    expect(validateValue('device_id', '256')).toEqual({ ok: false, code: 'range' })
    expect(validateValue('device_id', '010')).toEqual({ ok: true, value: 10 })
    expect(encodeValue('device_id', 10)).toBe('10')
    expect(validateValue('device_id', '-1')).toEqual({ ok: false, code: 'format' })
    expect(validateValue('device_id', '1.5')).toEqual({ ok: false, code: 'format' })
    expect(validateValue('source_id', '2')).toEqual({ ok: false, code: 'range' })
    expect(validateValue('low_batt_mv', '2499')).toEqual({ ok: false, code: 'range' })
    expect(validateValue('low_batt_mv', '4200')).toEqual({ ok: true, value: 4200 })
  })

  it('handles bool, enum and the 0-or-range calibration keys', () => {
    expect(validateValue('diag_log_enabled', '1')).toEqual({ ok: true, value: 1 })
    expect(validateValue('stream_enabled', '2')).toEqual({ ok: false, code: 'not-allowed' })
    expect(validateValue('stream_enabled', 'yes')).toEqual({ ok: false, code: 'format' })
    expect(validateValue('accel_fs_g', '16')).toEqual({ ok: true, value: 16 })
    expect(validateValue('accel_fs_g', '12')).toEqual({ ok: false, code: 'not-allowed' })
    expect(validateValue('gyro_fs_dps', '4000')).toEqual({ ok: true, value: 4000 })
    expect(validateValue('batt_cal_true_mv', '0')).toEqual({ ok: true, value: 0 })
    expect(validateValue('batt_cal_true_mv', '1000')).toEqual({ ok: false, code: 'range' })
    expect(validateValue('batt_cal_raw_mv', '3700')).toEqual({ ok: true, value: 3700 })
    expect(validateValue('batt_cal_raw_mv', '4501')).toEqual({ ok: false, code: 'range' })
  })

  it('canonicalises dotted quads', () => {
    expect(validateValue('udp_ip', '192.168.001.005')).toEqual({ ok: true, value: '192.168.1.5' })
    expect(validateValue('udp_ip', '256.1.1.1')).toEqual({ ok: false, code: 'range' })
    expect(validateValue('udp_ip', '1.2.3')).toEqual({ ok: false, code: 'format' })
    expect(validateValue('udp_ip', 'example.com')).toEqual({ ok: false, code: 'format' })
    expect(encodeValue('udp_ip', '010.001.1.1')).toBe('10.1.1.1')
  })

  it('stores tx power in quarter dB and prints it like write_qdbm_()', () => {
    expect(validateValue('wifi_tx_power_dbm', '8.5')).toEqual({ ok: true, value: 34 })
    expect(validateValue('wifi_tx_power_dbm', '12.25')).toEqual({ ok: true, value: 49 })
    expect(validateValue('wifi_tx_power_dbm', '8')).toEqual({ ok: true, value: 32 })
    expect(validateValue('wifi_tx_power_dbm', '20')).toEqual({ ok: true, value: 80 })
    expect(validateValue('wifi_tx_power_dbm', '1.9')).toEqual({ ok: false, code: 'range' })
    expect(validateValue('wifi_tx_power_dbm', '20.1')).toEqual({ ok: false, code: 'range' })
    expect(validateValue('wifi_tx_power_dbm', '8,5')).toEqual({ ok: false, code: 'format' })
    expect(encodeValue('wifi_tx_power_dbm', 34)).toBe('8.5')
    expect(encodeValue('wifi_tx_power_dbm', 32)).toBe('8')
    expect(encodeValue('wifi_tx_power_dbm', 49)).toBe('12.25')
    expect(encodeValue('wifi_tx_power_dbm', 83)).toBe('20.75')
    // A non-quarter value rounds like lroundf(x * 4) in the firmware.
    expect(validateValue('wifi_tx_power_dbm', '8.3')).toEqual({ ok: true, value: 33 })
  })

  it('refuses a line the fgets(160) reader would split', () => {
    expect(validateValue('device_id', '1'.repeat(148)).ok).toBe(false)
    expect(validateValue('device_id', '1'.repeat(160))).toEqual({ ok: false, code: 'line-too-long' })
  })
})

describe('firmwareReadsInt (strtoul base 0)', () => {
  it('shows what the firmware would use', () => {
    expect(firmwareReadsInt('5050')).toBe(5050)
    expect(firmwareReadsInt('0')).toBe(0)
    expect(firmwareReadsInt('010')).toBe(8)
    expect(firmwareReadsInt('08')).toBeNull()
    expect(firmwareReadsInt('0x1F')).toBe(31)
    expect(firmwareReadsInt('0X10')).toBe(16)
    expect(firmwareReadsInt('0x')).toBeNull()
    expect(firmwareReadsInt('+5')).toBe(5)
    expect(firmwareReadsInt('-0')).toBe(0)
    expect(firmwareReadsInt('-1')).toBe(0xffffffff)
    expect(firmwareReadsInt('')).toBeNull()
    expect(firmwareReadsInt('12abc')).toBeNull()
    expect(firmwareReadsInt('1 2')).toBeNull()
    expect(firmwareReadsInt(' \t7')).toBe(7)
    expect(firmwareReadsInt('99999999999')).toBe(0xffffffff)
  })
})

describe('firmwareReadsTxPower (strtof, quarter dB)', () => {
  it('shows what the firmware would store', () => {
    expect(firmwareReadsTxPower('8.5')).toBe(34)
    expect(firmwareReadsTxPower('8')).toBe(32)
    expect(firmwareReadsTxPower('1e1')).toBe(40)
    expect(firmwareReadsTxPower('.5e1')).toBe(20)
    expect(firmwareReadsTxPower('25')).toBeNull()
    expect(firmwareReadsTxPower('abc')).toBeNull()
    expect(firmwareReadsTxPower('8.5dBm')).toBeNull()
  })
})

describe('unit ids and sides', () => {
  it('builds and matches u<dev>-<src> like backend kinds.py', () => {
    expect(unitIdFrom(1, 0)).toBe('u1-0')
    expect(unitIdFrom(255, 1)).toBe('u255-1')
    expect(UNIT_ID_RE.test('u1-0')).toBe(true)
    expect(UNIT_ID_RE.test('u255-1')).toBe(true)
    expect(UNIT_ID_RE.test('u1000-0')).toBe(false)
    expect(UNIT_ID_RE.test('u1-2')).toBe(false)
    expect(UNIT_ID_RE.test('1-0')).toBe(false)
  })
})
