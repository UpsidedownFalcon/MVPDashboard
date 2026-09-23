import { describe, expect, it } from 'vitest'
import { applyEdits, hasBom, interpretAsFirmware, splitLines, stripBom, verifyReadback } from './configFile'
import { MAX_LINE_BYTES } from './configSchema'
import { FIRMWARE_SAMPLE_CONFIG, fixtureBytes, GENERATED_CONFIG_1_2_0 } from './fixtures/load'

const enc = new TextEncoder()
const dec = new TextDecoder('utf-8', { ignoreBOM: true })
const bytesOf = (s: string) => enc.encode(s)
const textOf = (b: Uint8Array) => dec.decode(b)
const sample = fixtureBytes(FIRMWARE_SAMPLE_CONFIG)
const generated = fixtureBytes(GENERATED_CONFIG_1_2_0)

function values(bytes: Uint8Array): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, e] of interpretAsFirmware(bytes).entries) out[k] = e.value
  return out
}

function crlfCount(bytes: Uint8Array): number {
  return splitLines(bytes).filter((l) => l.eolLen === 2).length
}

describe('fixtures', () => {
  it('firmware sample: 723 bytes, 23 CRLF lines, 9 keys, an em dash and en dash, no BOM', () => {
    expect(sample.length).toBe(723)
    expect(hasBom(sample)).toBe(false)
    const lines = splitLines(sample)
    expect(lines.length).toBe(23)
    expect(lines.every((l) => l.eolLen === 2)).toBe(true)
    expect(textOf(sample)).toContain('\u2014')
    expect(textOf(sample)).toContain('2500\u20134200')
    const view = interpretAsFirmware(sample)
    expect([...view.entries.keys()]).toEqual([
      'wifi_ssid',
      'wifi_password',
      'udp_ip',
      'udp_port',
      'device_id',
      'source_id',
      'diag_log_enabled',
      'stream_enabled',
      'low_batt_mv',
    ])
    expect(values(sample)).toMatchObject({
      wifi_ssid: '',
      wifi_password: '',
      udp_ip: '192.168.1.100',
      udp_port: '5050',
      device_id: '1',
      source_id: '0',
      diag_log_enabled: '1',
      stream_enabled: '1',
      low_batt_mv: '3100',
    })
    expect(view.unknownKeys).toEqual([])
    expect(view.malformedLines).toEqual([])
    expect(view.overlongLines).toEqual([])
    expect(view.duplicates).toEqual([])
  })

  it('generated 1.2.0 file: all 14 keys in firmware order with their defaults', () => {
    expect(hasBom(generated)).toBe(false)
    expect(splitLines(generated).every((l) => l.eolLen === 2)).toBe(true)
    expect(textOf(generated).startsWith('# NYKnicks knee sleeve data logger configuration\r\n')).toBe(true)
    expect(values(generated)).toEqual({
      wifi_ssid: 'Nirat',
      wifi_password: 'SS93MDrive',
      udp_ip: '192.168.1.100',
      udp_port: '5050',
      device_id: '1',
      source_id: '0',
      diag_log_enabled: '1',
      stream_enabled: '1',
      low_batt_mv: '3100',
      accel_fs_g: '32',
      gyro_fs_dps: '4000',
      wifi_tx_power_dbm: '8.5',
      batt_cal_true_mv: '0',
      batt_cal_raw_mv: '0',
    })
    expect(interpretAsFirmware(generated).unknownKeys).toEqual([])
  })
})

describe('splitLines', () => {
  it('splits at LF only and records the terminator length', () => {
    expect(splitLines(bytesOf('a\r\nbb\nccc'))).toEqual([
      { start: 0, end: 1, eolLen: 2 },
      { start: 3, end: 5, eolLen: 1 },
      { start: 6, end: 9, eolLen: 0 },
    ])
    expect(splitLines(bytesOf('x\n'))).toEqual([{ start: 0, end: 1, eolLen: 1 }])
    expect(splitLines(bytesOf(''))).toEqual([])
    expect(splitLines(bytesOf('\r\n'))).toEqual([{ start: 0, end: 0, eolLen: 2 }])
    // A bare CR is content to fgets(), not a line break.
    expect(splitLines(bytesOf('a=1\rb=2\n'))).toEqual([{ start: 0, end: 7, eolLen: 1 }])
  })
})

describe('interpretAsFirmware', () => {
  it('trims like C isspace, splits at the first "=", keeps inline "#"', () => {
    const v = interpretAsFirmware(bytesOf(' \tdevice_id =  3 \x0b\r\nwifi_ssid=cafe #1 = two\r\n'))
    expect(v.entries.get('device_id')).toEqual({ value: '3', lineIndex: 0, occurrences: 1 })
    expect(v.entries.get('wifi_ssid')?.value).toBe('cafe #1 = two')
  })

  it('skips whole-line # and ; comments and blank lines, flags malformed lines', () => {
    const v = interpretAsFirmware(bytesOf('# device_id=9\r\n  ; device_id=8\r\n\r\nnovalue\r\ndevice_id=1\r\n=orphan\r\n'))
    expect(v.entries.get('device_id')?.value).toBe('1')
    expect(v.malformedLines).toEqual([3])
    expect(v.entries.get('')?.value).toBe('orphan')
    expect(v.unknownKeys).toEqual([''])
  })

  it('lets the last duplicate win and reports it', () => {
    const v = interpretAsFirmware(bytesOf('device_id=1\r\nsource_id=0\r\ndevice_id=2\r\n'))
    expect(v.entries.get('device_id')).toEqual({ value: '2', lineIndex: 2, occurrences: 2 })
    expect(v.duplicates).toEqual(['device_id'])
  })

  it('reports unknown keys and a BOM glued to the first key', () => {
    const v = interpretAsFirmware(bytesOf('\ufeffwifi_ssid=x\r\nfoo=bar\r\n'))
    expect(v.bom).toBe(true)
    expect(v.entries.has('wifi_ssid')).toBe(false)
    expect(v.entries.get('\ufeffwifi_ssid')?.value).toBe('x')
    expect(v.unknownKeys).toEqual(['\ufeffwifi_ssid', 'foo'])
    expect(interpretAsFirmware(stripBom(bytesOf('\ufeffwifi_ssid=x\r\n'))).entries.get('wifi_ssid')?.value).toBe('x')
  })

  it('emulates the fgets(160) split of over-long lines', () => {
    const exact = bytesOf(`device_id=${'1'.repeat(MAX_LINE_BYTES - 'device_id='.length)}\r\nsource_id=1\r\n`)
    const okView = interpretAsFirmware(exact)
    expect(okView.overlongLines).toEqual([])
    expect(okView.entries.get('device_id')?.value.length).toBe(MAX_LINE_BYTES - 'device_id='.length)

    const long = bytesOf(`wifi_ssid=${'a'.repeat(170)}\r\nsource_id=1\r\n`)
    const v = interpretAsFirmware(long)
    expect(v.overlongLines).toEqual([0])
    expect(v.entries.get('wifi_ssid')?.value).toBe('a'.repeat(MAX_LINE_BYTES - 'wifi_ssid='.length))
    expect(v.malformedLines).toEqual([0])
    expect(v.entries.get('source_id')?.value).toBe('1')

    // A key hidden in the tail piece of a long junk line is seen by the firmware.
    const sneaky = bytesOf(`${'#'.repeat(1)}${'x'.repeat(MAX_LINE_BYTES - 1)}device_id=7\r\n`)
    const s = interpretAsFirmware(sneaky)
    expect(s.overlongLines).toEqual([0])
    expect(s.entries.get('device_id')?.value).toBe('7')
  })

  it('stops at a NUL byte like strlen()', () => {
    const v = interpretAsFirmware(bytesOf('device_id=1\0junk=2\r\nsource_id=1\r\n'))
    expect(v.entries.get('device_id')?.value).toBe('1')
    expect(v.entries.has('junk')).toBe(false)
  })
})

describe('applyEdits', () => {
  it('rewrites only the value spans of the firmware sample, CRLF preserved byte-for-byte', () => {
    const out = applyEdits(sample, { device_id: '7', wifi_ssid: 'Home' })
    const expected = textOf(sample).replace('device_id=1\r\n', 'device_id=7\r\n').replace('wifi_ssid=\r\n', 'wifi_ssid=Home\r\n')
    expect(textOf(out)).toBe(expected)
    expect(crlfCount(out)).toBe(crlfCount(sample))
    expect(out.length).toBe(sample.length + 4)
    expect(values(out)).toMatchObject({ device_id: '7', wifi_ssid: 'Home', udp_port: '5050' })
    expect(verifyReadback(out, { device_id: '7', wifi_ssid: 'Home' })).toEqual({ ok: true, mismatches: [] })
  })

  it('edits the LAST duplicate and leaves the first alone', () => {
    const out = applyEdits(bytesOf('device_id=1\r\n# note\r\ndevice_id=2\r\n'), { device_id: '9' })
    expect(textOf(out)).toBe('device_id=1\r\n# note\r\ndevice_id=9\r\n')
  })

  it('leaves comments, unknown keys and invalid UTF-8 untouched', () => {
    const head = bytesOf('# comment ')
    const junk = new Uint8Array([0xff, 0xfe, 0xc3])
    const body = bytesOf('\r\nfoo=b\u00e4r\r\ndevice_id=1\r\n')
    const input = new Uint8Array([...head, ...junk, ...body])
    const out = applyEdits(input, { device_id: '2' })
    const cut = input.length - 'device_id=1\r\n'.length
    expect([...out.subarray(0, cut + 'device_id='.length)]).toEqual([...input.subarray(0, cut + 'device_id='.length)])
    expect(textOf(out.subarray(cut))).toBe('device_id=2\r\n')
    expect([...out.subarray(head.length, head.length + 3)]).toEqual([0xff, 0xfe, 0xc3])
    expect(values(out)).toMatchObject({ foo: 'b\u00e4r', device_id: '2' })
  })

  it('appends missing keys in firmware order, after a separator when the file lacks a final EOL', () => {
    expect(textOf(applyEdits(bytesOf('device_id=1'), { stream_enabled: '0', accel_fs_g: '16' }))).toBe(
      'device_id=1\r\nstream_enabled=0\r\naccel_fs_g=16\r\n',
    )
    expect(textOf(applyEdits(bytesOf('device_id=1\n'), { stream_enabled: '0' }))).toBe('device_id=1\nstream_enabled=0\r\n')
    expect(textOf(applyEdits(bytesOf('device_id=1\r\n'), { stream_enabled: '0' }))).toBe('device_id=1\r\nstream_enabled=0\r\n')
    expect(textOf(applyEdits(new Uint8Array(0), { device_id: '3' }))).toBe('device_id=3\r\n')
    const out = applyEdits(sample, { accel_fs_g: '16' })
    expect(textOf(out)).toBe(`${textOf(sample)}accel_fs_g=16\r\n`)
  })

  it('never writes a BOM and never touches a commented key', () => {
    const out = applyEdits(bytesOf('# device_id=5\r\ndevice_id=1\r\n'), { device_id: '2', udp_port: '6000' })
    expect(hasBom(out)).toBe(false)
    expect(textOf(out)).toBe('# device_id=5\r\ndevice_id=2\r\nudp_port=6000\r\n')
    const bommed = bytesOf('\ufeffwifi_ssid=x\r\n')
    // The BOM'd key is not wifi_ssid to the firmware, so the edit appends.
    expect(textOf(applyEdits(bommed, { wifi_ssid: 'y' }))).toBe('\ufeffwifi_ssid=x\r\nwifi_ssid=y\r\n')
    expect(hasBom(stripBom(bommed))).toBe(false)
    expect(stripBom(sample)).toBe(sample)
  })

  it('replaces the whole physical value of an over-long line', () => {
    const out = applyEdits(bytesOf(`wifi_ssid=${'a'.repeat(170)}\r\nsource_id=1\r\n`), { wifi_ssid: 'home' })
    expect(textOf(out)).toBe('wifi_ssid=home\r\nsource_id=1\r\n')
  })

  it('keeps whitespace after "=" out of the value but leaves the rest of the line', () => {
    const out = applyEdits(bytesOf('device_id = 1  \r\n'), { device_id: '2' })
    expect(textOf(out)).toBe('device_id =2  \r\n')
    expect(values(out).device_id).toBe('2')
  })

  it('round-trips edits made with a value that is only whitespace after "="', () => {
    const out = applyEdits(bytesOf('wifi_ssid=   \r\n'), { wifi_ssid: 'net' })
    expect(textOf(out)).toBe('wifi_ssid=net   \r\n')
    expect(values(out).wifi_ssid).toBe('net')
  })
})

describe('verifyReadback', () => {
  it('reports the keys whose firmware value differs from the intent', () => {
    expect(verifyReadback(sample, { device_id: '1', udp_port: '5050' })).toEqual({ ok: true, mismatches: [] })
    expect(verifyReadback(sample, { device_id: '2', accel_fs_g: '16', udp_port: '5050' })).toEqual({
      ok: false,
      mismatches: ['device_id', 'accel_fs_g'],
    })
  })
})
