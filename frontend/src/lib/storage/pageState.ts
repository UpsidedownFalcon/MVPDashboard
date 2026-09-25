// Page state of Sleeve storage (PLAN_msd_management 4.1): a useReducer
// reducer, its actions and the selectors the page and components read. Pure
// (vitest, node): directory handles and AbortControllers live in refs in
// pages/Storage.tsx, which folds every async result into an action here.
//
// The config slice keeps the file bytes, the firmware's view of them and a
// DRAFT holding only the keys the user changed (a key typed back to its
// on-file value leaves the draft). Advanced keys refuse edits until the
// "I understand" acknowledgement (decision E); the "Point at this dashboard"
// button is the one curated exception (decision G).
import type { Device, UdpTarget } from '../api'
import { ACCEL_FS_ALLOWED_G, GYRO_FS_ALLOWED_DPS } from '../config'
import type { FileHeader, FileHeaderErrorCode } from './binFormat'
import { interpretAsFirmware, type FirmwareView } from './configFile'
import {
  CONFIG_KEYS,
  CONFIG_SPEC,
  DEVICE_ID_MAX,
  DEVICE_ID_MIN,
  encodeValue,
  firmwareReadsInt,
  firmwareReadsTxPower,
  SOURCE_ID_MAX,
  SOURCE_ID_MIN,
  unitIdFrom,
  validateValue,
  type ConfigKey,
  type ValidateCode,
} from './configSchema'
import type { PendingRaw } from './convert/pending'
import type { ConversionErrorCode } from './convert/protocol'
import type { OutputNames } from './convert/types'
import type { QueueEvent } from './convertQueue'
import { percent } from './format'
import type { LogKind } from './logNames'
import type {
  TransferErrorCode,
  TransferEvent,
  TransferItem,
  TransferPhase,
  TransferResult,
  TransferSummary,
} from './transfer'

/** Compiled-in defaults of app_config.h the firmware falls back to when a
 *  key is missing, unreadable or out of range. Format facts, not tunables. */
export const FIRMWARE_DEFAULT_DEVICE_ID = 1
export const FIRMWARE_DEFAULT_SOURCE_ID = 0
export const FIRMWARE_DEFAULT_ACCEL_FS_G = 32
export const FIRMWARE_DEFAULT_GYRO_FS_DPS = 4000

/** Decision K: `<dest>/sleeve-u<dev>-<src>/raw/`. */
export const FOLDER_PREFIX = 'sleeve-'

// ---------------------------------------------------------------------------
// State

export type SupportState = 'unknown' | 'ok' | 'unsupported'

export type DriveStatus = 'idle' | 'reconnect' | 'checking' | 'ready' | 'invalid' | 'busy'
export type DriveReason = 'no-config' | 'read-failed' | 'permission-denied'

export interface DriveState {
  status: DriveStatus
  name?: string
  /** CONFIG.TXT as actually spelled on the card. */
  configName?: string
  reason?: DriveReason
  detail?: string
}

export type HeaderError = FileHeaderErrorCode | 'read'

export interface LogRow {
  name: string
  size: number
  kind: LogKind
  session: number
  /** BIN rows: the parsed 512 B header. */
  header?: FileHeader
  headerError?: HeaderError
}

export interface SavedInfo {
  /** Keys whose firmware-read value differs between the old and new file. */
  changedKeys: ConfigKey[]
  needsPowerCycle: boolean
  /** Unit whose dashboard full scale was patched to match (decision I). */
  fsSynced?: string
  fsSyncFailed?: { unit: string; detail: string }
  identityChanged?: { from: string; to: string }
}

export type SaveErrorCode = 'write' | 'readback'

export interface ConfigState {
  bytes?: Uint8Array
  view?: FirmwareView
  draft: Partial<Record<ConfigKey, string>>
  dirty: boolean
  /** The user chose "Repair file": the BOM is stripped on save. */
  repairBom: boolean
  advancedOpen: boolean
  advancedAck: boolean
  saving: boolean
  saved?: SavedInfo
  error?: { code: SaveErrorCode; detail?: string }
}

export interface LogsState {
  entries: LogRow[]
  selected: string[]
}

export type DestStatus = 'none' | 'reconnect' | 'ready'

export interface DestState {
  status: DestStatus
  name?: string
}

export type ItemStatus = 'queued' | 'copying' | 'verifying' | 'deleting' | 'done' | 'failed' | 'cancelled'

export interface ItemState {
  status: ItemStatus
  result?: TransferResult
  deleted?: boolean
  code?: TransferErrorCode
  phase?: TransferPhase
  detail?: string
  localName?: string
  bytesDone: number
  bytesTotal: number
}

export interface RunProgress {
  bytesDone: number
  bytesTotal: number
  bytesPerS: number
  etaMs: number
}

export interface TransferState {
  running: boolean
  cancelling: boolean
  keepCopies: boolean
  /** Item ids in run order. */
  order: string[]
  items: Record<string, ItemState>
  run?: RunProgress
  summary?: TransferSummary
  /** The generator itself threw (never expected: it reports per item). */
  error?: string
}

/** Change-set 2 (agent-docs/03_PLAN_csv_summary 4.6). 'scanning' is the
 *  pipeline's pre-pass, 'converting' its main pass (assumption A5). */
export type ConversionStatus =
  | 'queued'
  | 'scanning'
  | 'converting'
  | 'converted'
  | 'already-converted'
  | 'failed'
  | 'cancelled'

export interface ConversionJob {
  /** conversionJobId(folder, localName). */
  id: string
  folder: string
  localName: string
  stem: string
  /** The sleeve the raw file belongs to, when known (header or folder name). */
  unitId: string | null
  status: ConversionStatus
  /** Whole-percent progress of the current phase. */
  pct: number
  /** CSV rows written so far. */
  rows: number
  /** The summary text, once converted (also on disk as outputs.summary). */
  summary?: string
  outputs?: OutputNames
  error?: { code: ConversionErrorCode; detail?: string }
}

/** What convert-queued needs; the reducer adds the progress fields. */
export type QueuedJob = Pick<ConversionJob, 'id' | 'folder' | 'localName' | 'stem' | 'unitId'>

export interface ConversionState {
  /** Job ids in the order they were first queued. */
  order: string[]
  jobs: Record<string, ConversionJob>
  /** The row whose summary is shown. */
  selected: string | null
  /** Decision O: raw files in the destination that lack an output. */
  pending: PendingRaw[]
  scanning: boolean
}

export interface StorageState {
  support: SupportState
  drive: DriveState
  config: ConfigState
  logs: LogsState
  dest: DestState
  transfer: TransferState
  conversion: ConversionState
}

export const initialState: StorageState = {
  support: 'unknown',
  drive: { status: 'idle' },
  config: {
    draft: {},
    dirty: false,
    repairBom: false,
    advancedOpen: false,
    advancedAck: false,
    saving: false,
  },
  logs: { entries: [], selected: [] },
  dest: { status: 'none' },
  transfer: { running: false, cancelling: false, keepCopies: false, order: [], items: {} },
  conversion: { order: [], jobs: {}, selected: null, pending: [], scanning: false },
}

/** One job per raw file on the PC; a retry reuses the id. */
export function conversionJobId(folder: string, localName: string): string {
  return `${folder}/${localName}`
}

// ---------------------------------------------------------------------------
// Actions

export type Action =
  | { type: 'support'; support: SupportState }
  | { type: 'drive-reconnect'; name?: string }
  | { type: 'drive-checking'; name?: string }
  | { type: 'drive-ready'; name?: string; configName: string; bytes: Uint8Array; entries: LogRow[] }
  | { type: 'drive-invalid'; reason: DriveReason; name?: string; detail?: string }
  | { type: 'logs-loaded'; entries: LogRow[] }
  | { type: 'edit'; key: ConfigKey; value: string }
  | { type: 'point-udp'; ip: string; port: number }
  | { type: 'repair-bom' }
  | { type: 'advanced-toggle'; open?: boolean }
  | { type: 'advanced-ack'; ack: boolean }
  | { type: 'save-start' }
  | { type: 'save-done'; bytes: Uint8Array; saved: SavedInfo }
  | { type: 'save-failed'; code: SaveErrorCode; detail?: string }
  | { type: 'save-fs-synced'; unit: string }
  | { type: 'save-fs-sync-failed'; unit: string; detail: string }
  | { type: 'select'; name: string; selected: boolean }
  | { type: 'select-all'; selected: boolean }
  | { type: 'dest-set'; name?: string }
  | { type: 'dest-reconnect'; name?: string }
  | { type: 'dest-clear' }
  | { type: 'keep-copies'; keep: boolean }
  | { type: 'transfer-start'; items: TransferItem[] }
  | { type: 'transfer-event'; event: TransferEvent }
  | { type: 'transfer-cancelling' }
  | { type: 'transfer-ended'; error?: string }
  | { type: 'convert-queued'; job: QueuedJob }
  | { type: 'convert-event'; event: QueueEvent }
  | { type: 'convert-select'; id: string }
  | { type: 'pending-scanning' }
  | { type: 'pending-loaded'; pending: PendingRaw[] }
  | { type: 'pending-failed' }

// ---------------------------------------------------------------------------
// Reducer

function freshConfig(bytes: Uint8Array, prev: ConfigState): ConfigState {
  return {
    ...initialState.config,
    bytes,
    view: interpretAsFirmware(bytes),
    // decision E: the acknowledgement lasts the page session, not one file
    advancedAck: prev.advancedAck,
    advancedOpen: prev.advancedOpen,
  }
}

function withDraft(config: ConfigState, draft: Partial<Record<ConfigKey, string>>): ConfigState {
  return {
    ...config,
    draft,
    dirty: Object.keys(draft).length > 0,
    saved: undefined,
    error: undefined,
  }
}

/** Set or clear one draft key: a value equal to the file's is not an edit. */
function setDraft(config: ConfigState, key: ConfigKey, value: string): Partial<Record<ConfigKey, string>> {
  const fileValue = config.view?.entries.get(key)?.value ?? ''
  const draft = { ...config.draft }
  if (value === fileValue) delete draft[key]
  else draft[key] = value
  return draft
}

function itemStatusFor(phase: TransferPhase): ItemStatus {
  switch (phase) {
    case 'probe':
    case 'copy':
      return 'copying'
    case 'verify':
    case 'dedupe':
      return 'verifying'
    case 'delete':
      return 'deleting'
  }
}

function foldTransfer(t: TransferState, event: TransferEvent): TransferState {
  const items = { ...t.items }
  switch (event.type) {
    case 'item-start': {
      const prev = items[event.id]
      items[event.id] = {
        ...prev,
        status: 'copying',
        bytesDone: 0,
        bytesTotal: event.bytesTotal,
      }
      return { ...t, items }
    }
    case 'progress': {
      const prev = items[event.id]
      if (prev) {
        items[event.id] = {
          ...prev,
          status: itemStatusFor(event.phase),
          phase: event.phase,
          // probe / verify progress belongs to the LOCAL file; the item bar
          // shows only bytes copied from the card
          bytesDone: event.phase === 'copy' ? event.bytesDone : prev.bytesDone,
          bytesTotal: event.bytesTotal,
        }
      }
      return {
        ...t,
        items,
        run: {
          bytesDone: event.runBytesDone,
          bytesTotal: event.runBytesTotal,
          bytesPerS: event.bytesPerS,
          etaMs: event.etaMs,
        },
      }
    }
    case 'item-done': {
      const prev = items[event.id]
      items[event.id] = {
        ...prev,
        status: 'done',
        result: event.result,
        deleted: event.deleted,
        localName: event.localName,
        bytesDone: prev?.bytesTotal ?? 0,
        bytesTotal: prev?.bytesTotal ?? 0,
      }
      return { ...t, items }
    }
    case 'item-failed': {
      const prev = items[event.id]
      items[event.id] = {
        ...prev,
        status: event.code === 'aborted' ? 'cancelled' : 'failed',
        code: event.code,
        phase: event.phase,
        detail: event.detail,
        localName: event.localName,
        bytesDone: prev?.bytesDone ?? 0,
        bytesTotal: prev?.bytesTotal ?? 0,
      }
      return { ...t, items }
    }
    case 'done': {
      if (event.summary.aborted) {
        for (const id of t.order) {
          if (items[id]?.status === 'queued') items[id] = { ...items[id], status: 'cancelled' }
        }
      }
      return { ...t, items, summary: event.summary, running: false, cancelling: false }
    }
  }
}

/** A pending entry is "live" once its job exists and has not failed or been
 *  cancelled: it is queued, running or already has its outputs. */
function isLiveJob(jobs: Record<string, ConversionJob>, folder: string, localName: string): boolean {
  const job = jobs[conversionJobId(folder, localName)]
  // only a job in flight is live: a finished one whose raw the scan finds
  // without outputs again (the user removed them) must be offered once more
  return job !== undefined && (job.status === 'queued' || job.status === 'scanning' || job.status === 'converting')
}

function foldConversion(c: ConversionState, event: QueueEvent): ConversionState {
  const prev = c.jobs[event.id]
  // a job this page never queued (or already forgot): nothing to update
  if (!prev) return c
  const jobs = { ...c.jobs }
  switch (event.type) {
    case 'queued':
      // convert-queued created the row already; the queue's echo adds nothing
      return c
    case 'progress':
      jobs[event.id] = {
        ...prev,
        status: event.phase === 'prepass' ? 'scanning' : 'converting',
        pct: percent(event.bytesDone, event.bytesTotal),
        rows: event.rows,
      }
      return { ...c, jobs }
    case 'done': {
      jobs[event.id] = {
        ...prev,
        status: 'converted',
        pct: 100,
        summary: event.summary,
        outputs: event.outputs,
        error: undefined,
      }
      // the latest summary shows itself unless the user is reading another
      const sel = c.selected === null ? undefined : c.jobs[c.selected]
      const keep = sel !== undefined && (sel.status === 'converted' || sel.status === 'already-converted')
      return { ...c, jobs, selected: keep ? c.selected : event.id }
    }
    case 'already-converted':
      jobs[event.id] = { ...prev, status: 'already-converted', pct: 100, outputs: event.outputs, error: undefined }
      return { ...c, jobs }
    case 'failed':
      jobs[event.id] = {
        ...prev,
        status: 'failed',
        error: { code: event.code, detail: event.detail },
        summary: undefined,
        outputs: undefined,
      }
      return { ...c, jobs }
    case 'cancelled':
      jobs[event.id] = { ...prev, status: 'cancelled', summary: undefined, outputs: undefined }
      return { ...c, jobs }
  }
}

export function reducer(s: StorageState, a: Action): StorageState {
  switch (a.type) {
    case 'support':
      return { ...s, support: a.support }

    case 'drive-reconnect':
      return { ...s, drive: { status: 'reconnect', name: a.name } }

    case 'drive-checking':
      return { ...s, drive: { status: 'checking', name: a.name } }

    case 'drive-ready':
      return {
        ...s,
        drive: { status: 'ready', name: a.name, configName: a.configName },
        config: freshConfig(a.bytes, s.config),
        logs: { entries: a.entries, selected: a.entries.map((e) => e.name) },
        transfer: { ...initialState.transfer, keepCopies: s.transfer.keepCopies },
      }

    case 'drive-invalid':
      return {
        ...s,
        drive: { status: 'invalid', name: a.name, reason: a.reason, detail: a.detail },
        config: { ...initialState.config, advancedAck: s.config.advancedAck },
        logs: { entries: [], selected: [] },
      }

    case 'logs-loaded': {
      // after a run: everything still on the card stays selected except what
      // just finished (kept copies would only be transferred again)
      const done = new Set(
        Object.entries(s.transfer.items)
          .filter(([, it]) => it.status === 'done')
          .map(([id]) => id),
      )
      const before = new Set(s.logs.selected)
      const selected = a.entries
        .map((e) => e.name)
        .filter((n) => !done.has(n) && (before.has(n) || !s.logs.entries.some((e) => e.name === n)))
      return { ...s, logs: { entries: a.entries, selected } }
    }

    case 'edit': {
      if (!s.config.view) return s
      if (CONFIG_SPEC[a.key].group === 'advanced' && !s.config.advancedAck) return s
      return { ...s, config: withDraft(s.config, setDraft(s.config, a.key, a.value)) }
    }

    case 'point-udp': {
      if (!s.config.view) return s
      const draft = setDraft(
        { ...s.config, draft: setDraft(s.config, 'udp_ip', a.ip) },
        'udp_port',
        String(a.port),
      )
      return { ...s, config: withDraft(s.config, draft) }
    }

    case 'repair-bom':
      if (!s.config.view?.bom) return s
      return { ...s, config: { ...s.config, repairBom: true, saved: undefined, error: undefined } }

    case 'advanced-toggle':
      return { ...s, config: { ...s.config, advancedOpen: a.open ?? !s.config.advancedOpen } }

    case 'advanced-ack':
      return { ...s, config: { ...s.config, advancedAck: a.ack } }

    case 'save-start':
      return {
        ...s,
        drive: { ...s.drive, status: 'busy' },
        config: { ...s.config, saving: true, saved: undefined, error: undefined },
      }

    case 'save-done':
      return {
        ...s,
        drive: { ...s.drive, status: 'ready' },
        config: { ...freshConfig(a.bytes, s.config), saved: a.saved },
      }

    case 'save-failed':
      return {
        ...s,
        drive: { ...s.drive, status: 'ready' },
        config: { ...s.config, saving: false, error: { code: a.code, detail: a.detail } },
      }

    case 'save-fs-synced':
      if (!s.config.saved) return s
      return { ...s, config: { ...s.config, saved: { ...s.config.saved, fsSynced: a.unit } } }

    case 'save-fs-sync-failed':
      if (!s.config.saved) return s
      return {
        ...s,
        config: {
          ...s.config,
          saved: { ...s.config.saved, fsSyncFailed: { unit: a.unit, detail: a.detail } },
        },
      }

    case 'select': {
      const has = s.logs.selected.includes(a.name)
      if (a.selected === has) return s
      const selected = a.selected
        ? s.logs.entries.map((e) => e.name).filter((n) => n === a.name || s.logs.selected.includes(n))
        : s.logs.selected.filter((n) => n !== a.name)
      return { ...s, logs: { ...s.logs, selected } }
    }

    case 'select-all':
      return {
        ...s,
        logs: { ...s.logs, selected: a.selected ? s.logs.entries.map((e) => e.name) : [] },
      }

    case 'dest-set':
      return { ...s, dest: { status: 'ready', name: a.name } }

    case 'dest-reconnect':
      return { ...s, dest: { status: 'reconnect', name: a.name } }

    case 'dest-clear':
      return { ...s, dest: { status: 'none' } }

    case 'keep-copies':
      return { ...s, transfer: { ...s.transfer, keepCopies: a.keep } }

    case 'transfer-start': {
      const items: Record<string, ItemState> = {}
      for (const it of a.items) items[it.id] = { status: 'queued', bytesDone: 0, bytesTotal: it.size }
      return {
        ...s,
        drive: { ...s.drive, status: 'busy' },
        transfer: {
          running: true,
          cancelling: false,
          keepCopies: s.transfer.keepCopies,
          order: a.items.map((it) => it.id),
          items,
          run: {
            bytesDone: 0,
            bytesTotal: a.items.reduce((sum, it) => sum + it.size, 0),
            bytesPerS: 0,
            etaMs: 0,
          },
        },
      }
    }

    case 'transfer-event':
      return { ...s, transfer: foldTransfer(s.transfer, a.event) }

    case 'transfer-cancelling':
      if (!s.transfer.running) return s
      return { ...s, transfer: { ...s.transfer, cancelling: true } }

    case 'transfer-ended':
      return {
        ...s,
        drive: { ...s.drive, status: s.drive.status === 'busy' ? 'ready' : s.drive.status },
        transfer: { ...s.transfer, running: false, cancelling: false, error: a.error },
      }

    case 'convert-queued': {
      const c = s.conversion
      // a re-run keeps the summary and outputs of its last success: they
      // describe files still on disk, and an 'already-converted' answer (the
      // same raw copied again) shows them; done replaces them, failed and
      // cancelled clear them
      const last = c.jobs[a.job.id]
      const job: ConversionJob = { ...a.job, status: 'queued', pct: 0, rows: 0, summary: last?.summary, outputs: last?.outputs }
      // a retry keeps its place in the list; a queued file is no longer "missing"
      const order = c.order.includes(job.id) ? c.order : [...c.order, job.id]
      const pending = c.pending.filter((p) => conversionJobId(p.folder, p.localName) !== job.id)
      return { ...s, conversion: { ...c, order, jobs: { ...c.jobs, [job.id]: job }, pending } }
    }

    case 'convert-event': {
      const conversion = foldConversion(s.conversion, a.event)
      return conversion === s.conversion ? s : { ...s, conversion }
    }

    case 'convert-select':
      if (!s.conversion.jobs[a.id]) return s
      return { ...s, conversion: { ...s.conversion, selected: a.id } }

    case 'pending-scanning':
      return { ...s, conversion: { ...s.conversion, scanning: true } }

    case 'pending-loaded':
      return {
        ...s,
        conversion: {
          ...s.conversion,
          scanning: false,
          pending: a.pending.filter((p) => !isLiveJob(s.conversion.jobs, p.folder, p.localName)),
        },
      }

    case 'pending-failed':
      return { ...s, conversion: { ...s.conversion, scanning: false, pending: [] } }
  }
}

// ---------------------------------------------------------------------------
// Selectors

export interface Identity {
  deviceId: number
  sourceId: number
  unitId: string
}

function intInRange(text: string | undefined, min: number, max: number, fallback: number): number {
  if (text === undefined) return fallback
  const v = firmwareReadsInt(text)
  return v !== null && v >= min && v <= max ? v : fallback
}

function identityFromTexts(deviceText: string | undefined, sourceText: string | undefined): Identity {
  const deviceId = intInRange(deviceText, DEVICE_ID_MIN, DEVICE_ID_MAX, FIRMWARE_DEFAULT_DEVICE_ID)
  const sourceId = intInRange(sourceText, SOURCE_ID_MIN, SOURCE_ID_MAX, FIRMWARE_DEFAULT_SOURCE_ID)
  return { deviceId, sourceId, unitId: unitIdFrom(deviceId, sourceId) }
}

/** Who the sleeve reports as, exactly as config_load() would resolve it:
 *  a missing, unreadable or out-of-range key falls back to the firmware
 *  default (device 1, source 0). */
export function identityOf(view: FirmwareView | undefined): Identity {
  return identityFromTexts(view?.entries.get('device_id')?.value, view?.entries.get('source_id')?.value)
}

/** The identity the draft would give the sleeve once saved. */
export function draftIdentity(state: StorageState): Identity {
  const values = effectiveDraft(state)
  return identityFromTexts(values.device_id, values.source_id)
}

/** Every key's current text: the draft where edited, else the file's value
 *  as the firmware reads it, else '' (missing). */
export function effectiveDraft(state: StorageState): Record<ConfigKey, string> {
  const out = {} as Record<ConfigKey, string>
  for (const spec of CONFIG_KEYS) {
    out[spec.key] = state.config.draft[spec.key] ?? state.config.view?.entries.get(spec.key)?.value ?? ''
  }
  return out
}

/** Validation failures of the draft keys only (the file's own values are
 *  surfaced as notices, never as errors on fields the user did not touch). */
export function draftProblems(state: StorageState): Partial<Record<ConfigKey, ValidateCode>> {
  const out: Partial<Record<ConfigKey, ValidateCode>> = {}
  for (const [key, text] of Object.entries(state.config.draft) as [ConfigKey, string][]) {
    const r = validateValue(key, text)
    if (!r.ok) out[key] = r.code
  }
  // A leading-zero value the sleeve cannot read at all ("08") has no decimal
  // it "already uses" to rewrite, so saving waits until the user replaces it.
  for (const o of octalKeys(state.config.view)) {
    if (o.reads === null && !(o.key in state.config.draft)) out[o.key] = 'format'
  }
  return out
}

export interface OctalKey {
  key: ConfigKey
  text: string
  /** What strtoul base 0 makes of it; null when it cannot ("08", "09"), in
   *  which case the sleeve silently falls back to its compiled default. */
  reads: number | null
}

const LEADING_ZERO_RE = /^0\d+$/

/** Integer keys written with a leading zero: the firmware reads them as
 *  octal. Saving rewrites them as plain decimal of the value the sleeve
 *  ALREADY uses, so nothing changes but the ambiguity. */
export function octalKeys(view: FirmwareView | undefined): OctalKey[] {
  if (!view) return []
  const out: OctalKey[] = []
  for (const spec of CONFIG_KEYS) {
    if (spec.kind !== 'int' && spec.kind !== 'bool' && spec.kind !== 'enum') continue
    const text = view.entries.get(spec.key)?.value
    if (text === undefined || !LEADING_ZERO_RE.test(text)) continue
    out.push({ key: spec.key, text, reads: firmwareReadsInt(text) })
  }
  return out
}

/** Something to write and nothing invalid to write it with. */
export function canSave(state: StorageState): boolean {
  const c = state.config
  if (!c.view || !c.bytes || c.saving) return false
  if (state.drive.status !== 'ready') return false
  if (state.transfer.running) return false
  const pending = c.dirty || c.repairBom || octalKeys(c.view).length > 0
  if (!pending) return false
  return Object.keys(draftProblems(state)).length === 0
}

/** The values applyEdits() writes: every draft key encoded canonically plus
 *  the octal rewrites. Empty when a draft value is invalid. */
export function buildEdits(state: StorageState): Record<string, string> {
  const edits: Record<string, string> = {}
  for (const [key, text] of Object.entries(state.config.draft) as [ConfigKey, string][]) {
    const r = validateValue(key, text)
    if (!r.ok) return {}
    edits[key] = encodeValue(key, r.value)
  }
  for (const o of octalKeys(state.config.view)) {
    if (o.reads !== null && !(o.key in edits)) edits[o.key] = encodeValue(o.key, o.reads)
  }
  return edits
}

/** The canonical reading of one key so two files compare by MEANING: "010"
 *  and "8" are the same device_id to the sleeve. */
function canonical(key: ConfigKey, text: string | undefined): string | null {
  if (text === undefined) return null
  switch (CONFIG_SPEC[key].kind) {
    case 'int':
    case 'bool':
    case 'enum': {
      const v = firmwareReadsInt(text)
      return v === null ? null : String(v)
    }
    case 'qdbm': {
      const v = firmwareReadsTxPower(text)
      return v === null ? null : String(v)
    }
    default:
      return text
  }
}

/** Keys whose firmware-read value differs between two files. */
export function changedKeysBetween(before: FirmwareView, after: FirmwareView): ConfigKey[] {
  const out: ConfigKey[] = []
  for (const spec of CONFIG_KEYS) {
    const a = canonical(spec.key, before.entries.get(spec.key)?.value)
    const b = canonical(spec.key, after.entries.get(spec.key)?.value)
    if (a !== b) out.push(spec.key)
  }
  return out
}

/** wifi_ssid / wifi_password only take effect after a power cycle. */
export function needsPowerCycle(keys: ConfigKey[]): boolean {
  return keys.some((k) => CONFIG_SPEC[k].effect === 'boot')
}

/** The full scale the sleeve will log at after a save, for decision I. */
export function fullScaleOf(view: FirmwareView): { accel_fs_g: number; gyro_fs_dps: number } {
  const accel = firmwareReadsInt(view.entries.get('accel_fs_g')?.value ?? '')
  const gyro = firmwareReadsInt(view.entries.get('gyro_fs_dps')?.value ?? '')
  return {
    accel_fs_g:
      accel !== null && (ACCEL_FS_ALLOWED_G as readonly number[]).includes(accel)
        ? accel
        : FIRMWARE_DEFAULT_ACCEL_FS_G,
    gyro_fs_dps:
      gyro !== null && (GYRO_FS_ALLOWED_DPS as readonly number[]).includes(gyro)
        ? gyro
        : FIRMWARE_DEFAULT_GYRO_FS_DPS,
  }
}

export type UdpStatus = 'this-dashboard' | 'other' | 'unknown'

/** Does the file (or the draft) stream to THIS dashboard? Compares the
 *  udp_ip text verbatim (the firmware does no IP normalisation) and the
 *  port as the firmware reads it. */
export function udpTargetStatus(
  source: FirmwareView | Partial<Record<ConfigKey, string>> | undefined,
  target: UdpTarget | undefined,
): UdpStatus {
  if (!target || target.ip === null) return 'unknown'
  if (!source) return 'other'
  const ip = 'entries' in source ? source.entries.get('udp_ip')?.value : source.udp_ip
  const portText = 'entries' in source ? source.entries.get('udp_port')?.value : source.udp_port
  const port = portText === undefined ? null : firmwareReadsInt(portText)
  return ip === target.ip && port === target.port ? 'this-dashboard' : 'other'
}

export function canStart(state: StorageState): boolean {
  return (
    state.drive.status === 'ready' &&
    state.dest.status === 'ready' &&
    !state.transfer.running &&
    !state.config.saving &&
    state.logs.selected.length > 0 &&
    state.logs.selected.every((n) => state.logs.entries.some((e) => e.name === n))
  )
}

export function folderFor(identity: Identity): string {
  return `${FOLDER_PREFIX}${identity.unitId}`
}

/** Identity a BIN carries in its own 512 B header. */
export function identityFromHeader(header: Pick<FileHeader, 'deviceId' | 'sourceId'>): Identity {
  return {
    deviceId: header.deviceId,
    sourceId: header.sourceId,
    unitId: unitIdFrom(header.deviceId, header.sourceId),
  }
}

const TXT_CFG_RE = /^#\s*cfg:.*\bdev=(\d+)\b.*\bsrc=(\d+)\b/m

/** Identity from a diagnostics log's `# cfg:` header line, for a TXT whose
 *  BIN is gone (PLAN 4.3). null when the line is absent or out of range. */
export function parseTxtIdentity(text: string): Identity | null {
  const m = TXT_CFG_RE.exec(text)
  if (!m) return null
  const deviceId = Number(m[1])
  const sourceId = Number(m[2])
  if (deviceId < DEVICE_ID_MIN || deviceId > DEVICE_ID_MAX) return null
  if (sourceId < SOURCE_ID_MIN || sourceId > SOURCE_ID_MAX) return null
  return { deviceId, sourceId, unitId: unitIdFrom(deviceId, sourceId) }
}

/** The soldier a unit belongs to: the rig that IS the unit (an unpaired
 *  sleeve) or the rig that lists it as a member. Demo soldiers carry no
 *  units and their ids never look like a unit id, so they never match. */
export function soldierNameFor(
  devices: Pick<Device, 'device_id' | 'display_name' | 'units'>[],
  unitId: string,
): string | null {
  const own = devices.find((d) => d.device_id === unitId)
  if (own) return own.display_name
  const host = devices.find((d) => d.units?.some((u) => u.unit_id === unitId))
  return host ? host.display_name : null
}

// ---- conversion (change-set 2) --------------------------------------------

/** The conversion rows in the order they were first queued. */
export function conversionRows(state: StorageState): ConversionJob[] {
  const out: ConversionJob[] = []
  for (const id of state.conversion.order) {
    const job = state.conversion.jobs[id]
    if (job) out.push(job)
  }
  return out
}

/** Raw files "Convert missing" would queue (decision O). */
export function missingCount(state: StorageState): number {
  return state.conversion.pending.length
}

export interface SelectedSummary {
  id: string
  folder: string
  /** `<stem>_summary.txt`, the file the text was written to. */
  file: string
  text: string
}

/** The summary the panel shows: the selected row's, once it is converted. */
export function selectedSummary(state: StorageState): SelectedSummary | null {
  const id = state.conversion.selected
  const job = id === null ? undefined : state.conversion.jobs[id]
  const finished = job?.status === 'converted' || job?.status === 'already-converted'
  if (!job || !finished || job.summary === undefined || !job.outputs) return null
  return { id: job.id, folder: job.folder, file: job.outputs.summary, text: job.summary }
}
