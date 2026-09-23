import { describe, expect, it } from 'vitest'
import type { UdpTarget } from '../api'
import { interpretAsFirmware } from './configFile'
import { fixtureBytes, LOG_0010_HEAD_TXT } from './fixtures/load'
import {
  buildEdits,
  canSave,
  canStart,
  changedKeysBetween,
  draftIdentity,
  draftProblems,
  effectiveDraft,
  folderFor,
  fullScaleOf,
  identityOf,
  initialState,
  needsPowerCycle,
  octalKeys,
  parseTxtIdentity,
  reducer,
  soldierNameFor,
  udpTargetStatus,
  type Action,
  type LogRow,
  type StorageState,
} from './pageState'
import type { TransferItem, TransferSummary } from './transfer'

const enc = new TextEncoder()

const CONFIG = [
  'wifi_ssid=Base',
  'wifi_password=secret',
  'udp_ip=10.0.0.5',
  'udp_port=5050',
  'device_id=7',
  'source_id=1',
  'diag_log_enabled=1',
  'stream_enabled=1',
  'low_batt_mv=3100',
  'accel_fs_g=32',
  'gyro_fs_dps=4000',
  'wifi_tx_power_dbm=8.5',
  '',
].join('\r\n')

const ROWS: LogRow[] = [
  { name: 'LOG_0010.BIN', size: 4608, kind: 'BIN', session: 10 },
  { name: 'LOG_0010.TXT', size: 300, kind: 'TXT', session: 10 },
  { name: 'LOG_0011.BIN', size: 4608, kind: 'BIN', session: 11 },
]

const ITEMS: TransferItem[] = ROWS.map((r) => ({
  id: r.name,
  name: r.name,
  size: r.size,
  kind: r.kind,
  folder: 'sleeve-u7-1',
}))

function run(actions: Action[], from: StorageState = initialState): StorageState {
  return actions.reduce(reducer, from)
}

function ready(config = CONFIG, entries = ROWS): StorageState {
  return run([
    { type: 'support', support: 'ok' },
    { type: 'drive-ready', name: 'HIPPOSDATA', configName: 'CONFIG.TXT', bytes: enc.encode(config), entries },
  ])
}

function summary(over: Partial<TransferSummary> = {}): TransferSummary {
  return {
    items: 3,
    copied: 3,
    alreadyTransferred: 0,
    failed: 0,
    deleted: 3,
    notStarted: 0,
    aborted: false,
    bytesCopied: 9516,
    elapsedMs: 10,
    ...over,
  }
}

describe('drive and config loading', () => {
  it('drive-ready interprets the file, selects every log and resets the draft', () => {
    const s = ready()
    expect(s.drive).toEqual({ status: 'ready', name: 'HIPPOSDATA', configName: 'CONFIG.TXT' })
    expect(s.config.view?.entries.get('device_id')?.value).toBe('7')
    expect(s.config.draft).toEqual({})
    expect(s.config.dirty).toBe(false)
    expect(s.logs.selected).toEqual(ROWS.map((r) => r.name))
  })

  it('drive-invalid drops the config and logs but keeps the Advanced acknowledgement', () => {
    const s = run(
      [
        { type: 'advanced-ack', ack: true },
        { type: 'drive-invalid', reason: 'no-config', name: 'Photos' },
      ],
      ready(),
    )
    expect(s.drive.status).toBe('invalid')
    expect(s.drive.reason).toBe('no-config')
    expect(s.config.view).toBeUndefined()
    expect(s.config.advancedAck).toBe(true)
    expect(s.logs.entries).toEqual([])
  })
})

describe('editing', () => {
  it('a basic edit marks the draft dirty and typing the file value back clears it', () => {
    const edited = reducer(ready(), { type: 'edit', key: 'wifi_ssid', value: 'Camp' })
    expect(edited.config.draft).toEqual({ wifi_ssid: 'Camp' })
    expect(edited.config.dirty).toBe(true)
    const reverted = reducer(edited, { type: 'edit', key: 'wifi_ssid', value: 'Base' })
    expect(reverted.config.draft).toEqual({})
    expect(reverted.config.dirty).toBe(false)
  })

  it('advanced keys refuse edits until "I understand" is ticked', () => {
    const blocked = reducer(ready(), { type: 'edit', key: 'low_batt_mv', value: '3300' })
    expect(blocked.config.draft).toEqual({})
    const allowed = run([
      { type: 'advanced-ack', ack: true },
      { type: 'edit', key: 'low_batt_mv', value: '3300' },
    ], ready())
    expect(allowed.config.draft).toEqual({ low_batt_mv: '3300' })
  })

  it('"Point at this dashboard" sets udp_ip and udp_port without the acknowledgement', () => {
    const s = reducer(ready(), { type: 'point-udp', ip: '203.0.113.9', port: 5005 })
    expect(s.config.draft).toEqual({ udp_ip: '203.0.113.9', udp_port: '5005' })
    expect(s.config.dirty).toBe(true)
    // pointing at what the file already holds is not an edit
    const same = reducer(ready(), { type: 'point-udp', ip: '10.0.0.5', port: 5050 })
    expect(same.config.dirty).toBe(false)
  })

  it('effectiveDraft overlays the draft on the file and reads missing keys as empty', () => {
    const s = reducer(ready(), { type: 'edit', key: 'device_id', value: '9' })
    const v = effectiveDraft(s)
    expect(v.device_id).toBe('9')
    expect(v.wifi_ssid).toBe('Base')
    expect(v.batt_cal_true_mv).toBe('')
  })

  it('repair-bom is only accepted when the file actually starts with a BOM', () => {
    expect(reducer(ready(), { type: 'repair-bom' }).config.repairBom).toBe(false)
    const bom = ready('﻿' + CONFIG)
    expect(bom.config.view?.bom).toBe(true)
    expect(reducer(bom, { type: 'repair-bom' }).config.repairBom).toBe(true)
  })
})

describe('identity', () => {
  it('reads device_id and source_id the way the firmware does', () => {
    expect(identityOf(ready().config.view)).toEqual({ deviceId: 7, sourceId: 1, unitId: 'u7-1' })
  })

  it('falls back to the firmware defaults (1, 0) for missing, unreadable or out-of-range keys', () => {
    expect(identityOf(undefined)).toEqual({ deviceId: 1, sourceId: 0, unitId: 'u1-0' })
    expect(identityOf(interpretAsFirmware(enc.encode('wifi_ssid=x\r\n')))).toEqual({
      deviceId: 1,
      sourceId: 0,
      unitId: 'u1-0',
    })
    expect(identityOf(interpretAsFirmware(enc.encode('device_id=300\r\nsource_id=abc\r\n'))).unitId).toBe('u1-0')
    // a leading zero is octal to strtoul base 0
    expect(identityOf(interpretAsFirmware(enc.encode('device_id=010\r\nsource_id=1\r\n')))).toEqual({
      deviceId: 8,
      sourceId: 1,
      unitId: 'u8-1',
    })
  })

  it('draftIdentity follows the draft', () => {
    const s = run([{ type: 'edit', key: 'device_id', value: '12' }, { type: 'edit', key: 'source_id', value: '0' }], ready())
    expect(draftIdentity(s).unitId).toBe('u12-0')
    expect(folderFor(draftIdentity(s))).toBe('sleeve-u12-0')
  })

  it('parses the # cfg: line of a diagnostics log', () => {
    const text = new TextDecoder().decode(fixtureBytes(LOG_0010_HEAD_TXT))
    expect(parseTxtIdentity(text)).toEqual({ deviceId: 1, sourceId: 0, unitId: 'u1-0' })
    expect(parseTxtIdentity('# fw=1.1.0\n')).toBeNull()
    expect(parseTxtIdentity('# cfg: dev=999 src=0\n')).toBeNull()
  })

  it('finds the soldier a unit belongs to, own rig first, then as a member', () => {
    const devices = [
      { device_id: 'u7-1', display_name: 'Alpha 1', units: [{ unit_id: 'u7-1' }] },
      { device_id: 'u3-0', display_name: 'Bravo 2', units: [{ unit_id: 'u3-0' }, { unit_id: 'u4-1' }] },
      { device_id: 'demo-1', display_name: 'Demo' },
    ] as Parameters<typeof soldierNameFor>[0]
    expect(soldierNameFor(devices, 'u7-1')).toBe('Alpha 1')
    expect(soldierNameFor(devices, 'u4-1')).toBe('Bravo 2')
    expect(soldierNameFor(devices, 'u9-0')).toBeNull()
  })
})

describe('saving', () => {
  it('canSave needs a valid pending change on a ready drive', () => {
    expect(canSave(ready())).toBe(false)
    const good = reducer(ready(), { type: 'edit', key: 'wifi_ssid', value: 'Camp' })
    expect(canSave(good)).toBe(true)
    const bad = reducer(ready(), { type: 'edit', key: 'device_id', value: '300' })
    expect(canSave(bad)).toBe(false)
    expect(draftProblems(bad)).toEqual({ device_id: 'range' })
    expect(canSave(reducer(good, { type: 'save-start' }))).toBe(false)
  })

  it('octal values are surfaced and rewritten as the decimal the sleeve already uses', () => {
    const s = ready(CONFIG.replace('device_id=7', 'device_id=010'))
    expect(octalKeys(s.config.view)).toEqual([{ key: 'device_id', text: '010', reads: 8 }])
    expect(canSave(s)).toBe(true)
    expect(buildEdits(s)).toEqual({ device_id: '8' })
  })

  it('a leading-zero value the sleeve cannot read blocks saving until it is replaced', () => {
    const s = ready(CONFIG.replace('device_id=7', 'device_id=08'))
    expect(octalKeys(s.config.view)).toEqual([{ key: 'device_id', text: '08', reads: null }])
    expect(draftProblems(s)).toEqual({ device_id: 'format' })
    expect(canSave(s)).toBe(false)
    expect(buildEdits(s)).toEqual({})
    const fixed = { ...s, config: { ...s.config, draft: { device_id: '8' }, dirty: true } }
    expect(draftProblems(fixed)).toEqual({})
    expect(canSave(fixed)).toBe(true)
    expect(buildEdits(fixed)).toEqual({ device_id: '8' })
  })

  it('buildEdits encodes the draft canonically and is empty when a value is invalid', () => {
    const s = run([
      { type: 'advanced-ack', ack: true },
      { type: 'edit', key: 'wifi_tx_power_dbm', value: '10.25' },
      { type: 'edit', key: 'udp_ip', value: '010.000.000.001' },
    ], ready())
    expect(buildEdits(s)).toEqual({ wifi_tx_power_dbm: '10.25', udp_ip: '10.0.0.1' })
    expect(buildEdits(reducer(s, { type: 'edit', key: 'udp_port', value: '0' }))).toEqual({})
  })

  it('save-done re-reads the file, clears the draft, keeps the acknowledgement and records the outcome', () => {
    const before = run([
      { type: 'advanced-ack', ack: true },
      { type: 'edit', key: 'wifi_ssid', value: 'Camp' },
      { type: 'save-start' },
    ], ready())
    expect(before.drive.status).toBe('busy')
    expect(before.config.saving).toBe(true)
    const after = reducer(before, {
      type: 'save-done',
      bytes: enc.encode(CONFIG.replace('wifi_ssid=Base', 'wifi_ssid=Camp')),
      saved: { changedKeys: ['wifi_ssid'], needsPowerCycle: true },
    })
    expect(after.drive.status).toBe('ready')
    expect(after.config.saving).toBe(false)
    expect(after.config.draft).toEqual({})
    expect(after.config.view?.entries.get('wifi_ssid')?.value).toBe('Camp')
    expect(after.config.advancedAck).toBe(true)
    expect(after.config.saved?.needsPowerCycle).toBe(true)
    const synced = reducer(after, { type: 'save-fs-synced', unit: 'u7-1' })
    expect(synced.config.saved?.fsSynced).toBe('u7-1')
  })

  it('save-failed keeps the draft and reports the error', () => {
    const s = run([
      { type: 'edit', key: 'wifi_ssid', value: 'Camp' },
      { type: 'save-start' },
      { type: 'save-failed', code: 'write', detail: 'NotAllowedError' },
    ], ready())
    expect(s.config.draft).toEqual({ wifi_ssid: 'Camp' })
    expect(s.config.error).toEqual({ code: 'write', detail: 'NotAllowedError' })
    expect(s.drive.status).toBe('ready')
  })

  it('changedKeysBetween compares by meaning and needsPowerCycle flags the WiFi keys', () => {
    const a = interpretAsFirmware(enc.encode(CONFIG))
    const octal = interpretAsFirmware(enc.encode(CONFIG.replace('device_id=7', 'device_id=07')))
    expect(changedKeysBetween(a, octal)).toEqual([])
    const b = interpretAsFirmware(
      enc.encode(CONFIG.replace('wifi_password=secret', 'wifi_password=other').replace('accel_fs_g=32', 'accel_fs_g=16')),
    )
    expect(changedKeysBetween(a, b)).toEqual(['wifi_password', 'accel_fs_g'])
    expect(needsPowerCycle(['wifi_password'])).toBe(true)
    expect(needsPowerCycle(['accel_fs_g', 'udp_port'])).toBe(false)
    expect(fullScaleOf(b)).toEqual({ accel_fs_g: 16, gyro_fs_dps: 4000 })
    expect(fullScaleOf(interpretAsFirmware(enc.encode('accel_fs_g=3\r\n')))).toEqual({
      accel_fs_g: 32,
      gyro_fs_dps: 4000,
    })
  })
})

describe('udpTargetStatus', () => {
  const target: UdpTarget = { ip: '10.0.0.5', port: 5050, source: 'env' }

  it('matches the file or the draft against the dashboard target', () => {
    expect(udpTargetStatus(ready().config.view, target)).toBe('this-dashboard')
    expect(udpTargetStatus(ready().config.view, { ...target, port: 5005 })).toBe('other')
    expect(udpTargetStatus({ udp_ip: '10.0.0.5', udp_port: '5050' }, target)).toBe('this-dashboard')
    expect(udpTargetStatus({ udp_ip: '10.0.0.6', udp_port: '5050' }, target)).toBe('other')
  })

  it('is unknown when the dashboard could not resolve its own address', () => {
    expect(udpTargetStatus(ready().config.view, { ip: null, port: 5050, source: 'unresolved' })).toBe('unknown')
    expect(udpTargetStatus(ready().config.view, undefined)).toBe('unknown')
  })
})

describe('log selection and transfer', () => {
  it('select and select-all keep listing order', () => {
    let s = reducer(ready(), { type: 'select-all', selected: false })
    expect(s.logs.selected).toEqual([])
    s = reducer(s, { type: 'select', name: 'LOG_0011.BIN', selected: true })
    s = reducer(s, { type: 'select', name: 'LOG_0010.BIN', selected: true })
    expect(s.logs.selected).toEqual(['LOG_0010.BIN', 'LOG_0011.BIN'])
    s = reducer(s, { type: 'select', name: 'LOG_0010.BIN', selected: false })
    expect(s.logs.selected).toEqual(['LOG_0011.BIN'])
  })

  it('canStart needs a ready drive, a destination and a selection', () => {
    expect(canStart(ready())).toBe(false)
    const withDest = reducer(ready(), { type: 'dest-set', name: 'Logs' })
    expect(canStart(withDest)).toBe(true)
    expect(canStart(reducer(withDest, { type: 'select-all', selected: false }))).toBe(false)
    expect(canStart(reducer(withDest, { type: 'transfer-start', items: ITEMS }))).toBe(false)
  })

  it('transfer-start queues every item and marks the drive busy', () => {
    const s = reducer(ready(), { type: 'transfer-start', items: ITEMS })
    expect(s.drive.status).toBe('busy')
    expect(s.transfer.running).toBe(true)
    expect(s.transfer.order).toEqual(ITEMS.map((i) => i.id))
    expect(s.transfer.items['LOG_0010.BIN']).toEqual({ status: 'queued', bytesDone: 0, bytesTotal: 4608 })
    expect(s.transfer.run?.bytesTotal).toBe(9516)
  })

  it('folds progress into the item and the run', () => {
    const s = run([
      { type: 'transfer-start', items: ITEMS },
      { type: 'transfer-event', event: { type: 'item-start', id: 'LOG_0010.BIN', name: 'LOG_0010.BIN', bytesTotal: 4608 } },
      {
        type: 'transfer-event',
        event: {
          type: 'progress',
          id: 'LOG_0010.BIN',
          phase: 'copy',
          bytesDone: 512,
          bytesTotal: 4608,
          bytesPerS: 1e6,
          etaMs: 9004,
          runBytesDone: 512,
          runBytesTotal: 9516,
        },
      },
    ], ready())
    expect(s.transfer.items['LOG_0010.BIN']).toMatchObject({ status: 'copying', phase: 'copy', bytesDone: 512 })
    expect(s.transfer.run).toEqual({ bytesDone: 512, bytesTotal: 9516, bytesPerS: 1e6, etaMs: 9004 })
    // verify progress reports the LOCAL re-read: the item bar stays at the card bytes
    const verifying = reducer(s, {
      type: 'transfer-event',
      event: {
        type: 'progress',
        id: 'LOG_0010.BIN',
        phase: 'verify',
        bytesDone: 4096,
        bytesTotal: 4608,
        bytesPerS: 1e6,
        etaMs: 4000,
        runBytesDone: 4608,
        runBytesTotal: 9516,
      },
    })
    expect(verifying.transfer.items['LOG_0010.BIN']).toMatchObject({ status: 'verifying', bytesDone: 512 })
  })

  it('item-done and item-failed land on the right statuses', () => {
    const base = run([
      { type: 'transfer-start', items: ITEMS },
      { type: 'transfer-event', event: { type: 'item-start', id: 'LOG_0010.BIN', name: 'LOG_0010.BIN', bytesTotal: 4608 } },
    ], ready())
    const done = reducer(base, {
      type: 'transfer-event',
      event: { type: 'item-done', id: 'LOG_0010.BIN', result: 'copied', deleted: true, localName: 'LOG_0010.BIN' },
    })
    expect(done.transfer.items['LOG_0010.BIN']).toMatchObject({
      status: 'done',
      result: 'copied',
      deleted: true,
      bytesDone: 4608,
    })
    const failed = reducer(base, {
      type: 'transfer-event',
      event: { type: 'item-failed', id: 'LOG_0010.BIN', code: 'card-read', phase: 'copy', detail: 'NotReadableError' },
    })
    expect(failed.transfer.items['LOG_0010.BIN']).toMatchObject({ status: 'failed', code: 'card-read', phase: 'copy' })
    const aborted = reducer(base, {
      type: 'transfer-event',
      event: { type: 'item-failed', id: 'LOG_0010.BIN', code: 'aborted', phase: 'copy' },
    })
    expect(aborted.transfer.items['LOG_0010.BIN'].status).toBe('cancelled')
  })

  it('done after an abort cancels the queued items and ends the run', () => {
    const s = run([
      { type: 'transfer-start', items: ITEMS },
      { type: 'transfer-cancelling' },
      {
        type: 'transfer-event',
        event: { type: 'done', summary: summary({ copied: 0, deleted: 0, aborted: true, notStarted: 3, failed: 0 }) },
      },
      { type: 'transfer-ended' },
    ], ready())
    expect(s.transfer.running).toBe(false)
    expect(s.transfer.cancelling).toBe(false)
    expect(s.transfer.summary?.aborted).toBe(true)
    expect(Object.values(s.transfer.items).map((i) => i.status)).toEqual(['cancelled', 'cancelled', 'cancelled'])
    expect(s.drive.status).toBe('ready')
  })

  it('logs-loaded after a run deselects what just finished and keeps the rest', () => {
    const s = run([
      { type: 'transfer-start', items: ITEMS },
      {
        type: 'transfer-event',
        event: { type: 'item-done', id: 'LOG_0010.BIN', result: 'copied', deleted: false, localName: 'LOG_0010.BIN' },
      },
      { type: 'transfer-event', event: { type: 'done', summary: summary({ copied: 1, deleted: 0, failed: 2 }) } },
      { type: 'transfer-ended' },
      { type: 'logs-loaded', entries: ROWS },
    ], ready())
    expect(s.logs.selected).toEqual(['LOG_0010.TXT', 'LOG_0011.BIN'])
  })

  it('transfer-ended with an error records it', () => {
    const s = run([{ type: 'transfer-start', items: ITEMS }, { type: 'transfer-ended', error: 'boom' }], ready())
    expect(s.transfer.running).toBe(false)
    expect(s.transfer.error).toBe('boom')
  })
})
