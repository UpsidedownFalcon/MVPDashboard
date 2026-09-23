// Sleeve storage (PLAN_msd_management 4.2 / 4.3, decision D): the /storage
// page. Owns the reducer (lib/storage/pageState), the directory handles and
// the AbortController (refs: not serialisable, never in state), the queries
// (['units'], ['devices'] via useMergedDevices, ['udp-target']) and the
// decision I mutation. Every file operation goes through fsaDir() so the
// engine and the CONFIG.TXT model stay pure. Nothing here ever passes a demo
// id to the API: a unit id is `u<dev>-<src>` by construction and is checked
// against UNIT_ID_RE before the one PATCH this page can make.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useReducer, useRef } from 'react'
import ConfigEditor from '../components/storage/ConfigEditor'
import DrivePanel from '../components/storage/DrivePanel'
import LogList from '../components/storage/LogList'
import TransferPanel from '../components/storage/TransferPanel'
import { ApiError, fetchUdpTarget, fetchUnits, patchUnit } from '../lib/api'
import {
  STORAGE_READ_CHUNK_BYTES,
  STORAGE_TXT_CFG_PROBE_BYTES,
  STORAGE_UDP_TARGET_STALE_MS,
} from '../lib/config'
import { useMergedDevices } from '../lib/devices'
import { RIG_QUERY_KEYS } from '../lib/rig'
import { FILE_HEADER_BYTES, parseFileHeader } from '../lib/storage/binFormat'
import { applyEdits, interpretAsFirmware, stripBom, verifyReadback } from '../lib/storage/configFile'
import { UNIT_ID_RE } from '../lib/storage/configSchema'
import { STORAGE_COPY } from '../lib/storage/copy'
import { fsaDir, isSupported, pickDirectory, queryPermission, requestPermission } from '../lib/storage/fsa'
import { loadHandle, saveHandle } from '../lib/storage/handleStore'
import type { DirEntry, DirLike } from '../lib/storage/io'
import { findConfigEntry, groupSessions, isIgnoredEntry } from '../lib/storage/logNames'
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
  identityFromHeader,
  identityOf,
  initialState,
  needsPowerCycle,
  parseTxtIdentity,
  reducer,
  soldierNameFor,
  udpTargetStatus,
  type DriveReason,
  type Identity,
  type LogRow,
  type SavedInfo,
} from '../lib/storage/pageState'
import { errorDetail as detailOf, errorName } from '../lib/storage/io'
import { runTransfer, type TransferItem } from '../lib/storage/transfer'

const decoder = new TextDecoder()

/** An ApiError already carries the server's detail; anything else gets the
 *  shared "Name: message" rendering from lib/storage/io.ts. */
function errorDetail(err: unknown): string {
  return err instanceof ApiError ? err.message : detailOf(err)
}

function isPermissionError(err: unknown): boolean {
  const n = errorName(err)
  return n === 'NotAllowedError' || n === 'SecurityError'
}

function reasonFor(err: unknown): DriveReason {
  return isPermissionError(err) ? 'permission-denied' : 'read-failed'
}

/** Files in the root, minus swap files, system folders and dotfiles. */
async function listFiles(dir: DirLike): Promise<DirEntry[]> {
  return (await dir.list()).filter((e) => e.kind === 'file' && !isIgnoredEntry(e.name))
}

/** A BIN row with its 512 B header; a header that cannot be read or parsed
 *  marks the row rather than failing the listing. */
async function binRow(dir: DirLike, name: string, size: number, session: number): Promise<LogRow> {
  const row: LogRow = { name, size, kind: 'BIN', session }
  try {
    const src = await dir.open(name)
    const parsed = parseFileHeader(await src.read(0, FILE_HEADER_BYTES))
    if (parsed.ok) row.header = parsed.header
    else row.headerError = parsed.code
  } catch {
    row.headerError = 'read'
  }
  return row
}

async function listLogs(dir: DirLike, listing: DirEntry[]): Promise<LogRow[]> {
  const rows: LogRow[] = []
  for (const s of groupSessions(listing)) {
    if (s.bin) rows.push(await binRow(dir, s.bin.name, s.bin.size, s.session))
    if (s.txt) rows.push({ name: s.txt.name, size: s.txt.size, kind: 'TXT', session: s.session })
  }
  return rows
}

type CardRead =
  | { configName: string; bytes: Uint8Array; entries: LogRow[] }
  | { reason: DriveReason; detail?: string }

/** Validate a picked folder as the HIPPOSDATA root and read what the page
 *  needs: CONFIG.TXT (as spelled on the card) and the log listing. */
async function readCard(dir: DirLike): Promise<CardRead> {
  try {
    const listing = await listFiles(dir)
    const configName = findConfigEntry(listing.map((e) => e.name))
    if (!configName) return { reason: 'no-config' }
    const cfg = await dir.open(configName)
    const bytes = await cfg.read(0, cfg.size)
    return { configName, bytes, entries: await listLogs(dir, listing) }
  } catch (err) {
    return { reason: reasonFor(err), detail: errorDetail(err) }
  }
}

/** Identity for a TXT whose BIN is gone: its own `# cfg:` line (4.3). */
async function txtIdentity(dir: DirLike, name: string): Promise<Identity | null> {
  try {
    const src = await dir.open(name)
    return parseTxtIdentity(decoder.decode(await src.read(0, STORAGE_TXT_CFG_PROBE_BYTES)))
  } catch {
    return null
  }
}

async function permissionOf(handle: FileSystemHandle): Promise<PermissionState> {
  try {
    return await queryPermission(handle)
  } catch {
    return 'denied'
  }
}

export default function Storage() {
  const [state, dispatch] = useReducer(reducer, initialState)
  const queryClient = useQueryClient()
  const cardRef = useRef<FileSystemDirectoryHandle | null>(null)
  const destRef = useRef<FileSystemDirectoryHandle | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  /** False once unmounted: late async results are dropped, not dispatched. */
  const alive = useRef(true)

  const supported = state.support === 'ok'
  const { devices } = useMergedDevices()
  const unitsQuery = useQuery({
    queryKey: ['units'],
    queryFn: () => fetchUnits(),
    enabled: supported,
  })
  const udpQuery = useQuery({
    queryKey: ['udp-target'],
    queryFn: fetchUdpTarget,
    staleTime: STORAGE_UDP_TARGET_STALE_MS,
    enabled: supported,
  })

  const openDrive = useCallback(async (handle: FileSystemDirectoryHandle) => {
    dispatch({ type: 'drive-checking', name: handle.name })
    const r = await readCard(fsaDir(handle))
    if (!alive.current) return
    if ('reason' in r) {
      dispatch({ type: 'drive-invalid', reason: r.reason, detail: r.detail, name: handle.name })
    } else {
      dispatch({
        type: 'drive-ready',
        name: handle.name,
        configName: r.configName,
        bytes: r.bytes,
        entries: r.entries,
      })
    }
  }, [])

  // Mount: support check, then the persisted handles. A handle that still
  // has permission loads at once; one that needs a prompt waits for a click
  // (requestPermission needs a user gesture).
  useEffect(() => {
    alive.current = true
    let cancelled = false
    if (!isSupported()) {
      dispatch({ type: 'support', support: 'unsupported' })
      return
    }
    dispatch({ type: 'support', support: 'ok' })
    void (async () => {
      const card = await loadHandle('sleeve')
      if (cancelled) return
      if (card) {
        cardRef.current = card
        const p = await permissionOf(card)
        if (cancelled) return
        if (p === 'granted') await openDrive(card)
        else dispatch({ type: 'drive-reconnect', name: card.name })
      }
      const dest = await loadHandle('dest')
      if (cancelled || !dest) return
      destRef.current = dest
      const p = await permissionOf(dest)
      if (cancelled) return
      dispatch(p === 'granted' ? { type: 'dest-set', name: dest.name } : { type: 'dest-reconnect', name: dest.name })
    })()
    return () => {
      cancelled = true
      alive.current = false
      abortRef.current?.abort()
    }
  }, [openDrive])

  // ---- drive -------------------------------------------------------------

  const onOpen = async () => {
    let handle: FileSystemDirectoryHandle | null
    try {
      handle = await pickDirectory('sleeve')
    } catch (err) {
      dispatch({ type: 'drive-invalid', reason: 'permission-denied', detail: errorDetail(err) })
      return
    }
    if (!handle) return
    cardRef.current = handle
    await saveHandle('sleeve', handle)
    await openDrive(handle)
  }

  const onReconnect = async () => {
    const handle = cardRef.current
    if (!handle) return
    let p: PermissionState = 'denied'
    try {
      p = await requestPermission(handle)
    } catch {
      /* treated as denied */
    }
    if (p === 'granted') await openDrive(handle)
    else dispatch({ type: 'drive-invalid', reason: 'permission-denied', name: handle.name })
  }

  const relist = async (handle: FileSystemDirectoryHandle) => {
    try {
      const dir = fsaDir(handle)
      const entries = await listLogs(dir, await listFiles(dir))
      if (alive.current) dispatch({ type: 'logs-loaded', entries })
    } catch (err) {
      if (alive.current) {
        dispatch({ type: 'drive-invalid', reason: reasonFor(err), name: handle.name, detail: errorDetail(err) })
      }
    }
  }

  // ---- destination -------------------------------------------------------

  const onPickDest = async () => {
    let handle: FileSystemDirectoryHandle | null
    try {
      handle = await pickDirectory('dest')
    } catch {
      return
    }
    if (!handle) return
    destRef.current = handle
    await saveHandle('dest', handle)
    dispatch({ type: 'dest-set', name: handle.name })
  }

  const onReconnectDest = async () => {
    const handle = destRef.current
    if (!handle) return
    let p: PermissionState = 'denied'
    try {
      p = await requestPermission(handle)
    } catch {
      /* treated as denied */
    }
    if (p === 'granted') dispatch({ type: 'dest-set', name: handle.name })
    else dispatch({ type: 'dest-clear' })
  }

  // ---- save (4.2) --------------------------------------------------------

  // decision I: keep the dashboard's stored full scale equal to the card's
  const syncUnit = useMutation({
    mutationFn: ({
      unitId,
      body,
    }: {
      unitId: string
      rigId?: string
      body: { accel_fs_g?: number; gyro_fs_dps?: number }
    }) => patchUnit(unitId, body),
    onSuccess: (_data, v) => {
      dispatch({ type: 'save-fs-synced', unit: v.unitId })
      void queryClient.invalidateQueries({ queryKey: ['units'] })
      void queryClient.invalidateQueries({ queryKey: ['devices'] })
      if (v.rigId) {
        for (const key of RIG_QUERY_KEYS) void queryClient.invalidateQueries({ queryKey: [key, v.rigId] })
      }
    },
    onError: (err, v) => dispatch({ type: 'save-fs-sync-failed', unit: v.unitId, detail: errorDetail(err) }),
  })

  const onSave = async () => {
    const handle = cardRef.current
    const { config, drive } = state
    if (!handle || !config.bytes || !config.view || !drive.configName || !canSave(state)) return
    const edits = buildEdits(state)
    const before = config.view
    dispatch({ type: 'save-start' })
    const dir = fsaDir(handle)
    let back: Uint8Array
    try {
      const out = applyEdits(config.repairBom ? stripBom(config.bytes) : config.bytes, edits)
      const sink = await dir.create(drive.configName)
      try {
        await sink.write(out)
        await sink.close()
      } catch (err) {
        await sink.abort().catch(() => undefined)
        throw err
      }
      const src = await dir.open(drive.configName)
      back = await src.read(0, src.size)
    } catch (err) {
      if (alive.current) dispatch({ type: 'save-failed', code: 'write', detail: errorDetail(err) })
      return
    }
    if (!alive.current) return
    const check = verifyReadback(back, edits)
    if (!check.ok) {
      dispatch({ type: 'save-failed', code: 'readback', detail: check.mismatches.join(', ') })
      return
    }
    const after = interpretAsFirmware(back)
    const changedKeys = changedKeysBetween(before, after)
    const from = identityOf(before)
    const to = identityOf(after)
    const saved: SavedInfo = {
      changedKeys,
      needsPowerCycle: needsPowerCycle(changedKeys),
      identityChanged: from.unitId !== to.unitId ? { from: from.unitId, to: to.unitId } : undefined,
    }
    dispatch({ type: 'save-done', bytes: back, saved })

    const accelChanged = changedKeys.includes('accel_fs_g')
    const gyroChanged = changedKeys.includes('gyro_fs_dps')
    const unit = unitsQuery.data?.find((u) => u.unit_id === to.unitId)
    if ((accelChanged || gyroChanged) && unit && UNIT_ID_RE.test(to.unitId)) {
      const fs = fullScaleOf(after)
      syncUnit.mutate({
        unitId: to.unitId,
        rigId: unit.rig_id,
        body: {
          ...(accelChanged ? { accel_fs_g: fs.accel_fs_g } : {}),
          ...(gyroChanged ? { gyro_fs_dps: fs.gyro_fs_dps } : {}),
        },
      })
    }
  }

  // ---- transfer (4.3) ----------------------------------------------------

  const onStart = async () => {
    const card = cardRef.current
    const dest = destRef.current
    if (!card || !dest || !canStart(state)) return
    const cardDir = fsaDir(card)
    const destDir = fsaDir(dest)
    const fallback = identityOf(state.config.view)
    const chosen = new Set(state.logs.selected)
    const items: TransferItem[] = []
    for (const row of state.logs.entries) {
      if (!chosen.has(row.name)) continue
      let ident: Identity | null = row.header ? identityFromHeader(row.header) : null
      if (!ident && row.kind === 'TXT') {
        const bin = state.logs.entries.find((r) => r.kind === 'BIN' && r.session === row.session)
        ident = bin?.header ? identityFromHeader(bin.header) : await txtIdentity(cardDir, row.name)
      }
      items.push({
        id: row.name,
        name: row.name,
        size: row.size,
        kind: row.kind,
        folder: folderFor(ident ?? fallback),
      })
    }
    const ctrl = new AbortController()
    abortRef.current = ctrl
    dispatch({ type: 'transfer-start', items })
    try {
      const events = runTransfer(
        items,
        { card: cardDir, dest: destDir, keepCopies: state.transfer.keepCopies },
        { signal: ctrl.signal, chunkBytes: STORAGE_READ_CHUNK_BYTES },
      )
      for await (const event of events) {
        if (!alive.current) break
        dispatch({ type: 'transfer-event', event })
      }
      if (alive.current) dispatch({ type: 'transfer-ended' })
    } catch (err) {
      if (alive.current) dispatch({ type: 'transfer-ended', error: errorDetail(err) })
    } finally {
      abortRef.current = null
    }
    if (alive.current) await relist(card)
  }

  const onCancel = () => {
    abortRef.current?.abort()
    dispatch({ type: 'transfer-cancelling' })
  }

  // ---- derived -----------------------------------------------------------

  const values = effectiveDraft(state)
  const problems = draftProblems(state)
  const identity = identityOf(state.config.view)
  const soldierName = soldierNameFor(devices, identity.unitId)
  const wanted = draftIdentity(state)
  const duplicateWarning =
    wanted.unitId !== identity.unitId && (unitsQuery.data ?? []).some((u) => u.unit_id === wanted.unitId)
  const udpTarget = udpQuery.data
  const busy = state.config.saving || state.transfer.running
  const driveOpen = (state.drive.status === 'ready' || state.drive.status === 'busy') && !!state.config.view

  return (
    <div className="storage-page">
      <header className="storage-head">
        <h1 className="storage-title">{STORAGE_COPY.title}</h1>
      </header>

      <DrivePanel
        support={state.support}
        drive={state.drive}
        busy={busy}
        onOpen={() => void onOpen()}
        onReconnect={() => void onReconnect()}
      />

      {driveOpen && (
        <>
          <ConfigEditor
            config={state.config}
            values={values}
            problems={problems}
            identity={identity}
            soldierName={soldierName}
            duplicateWarning={duplicateWarning}
            udpStatus={udpTargetStatus(values, udpTarget)}
            udpTarget={udpTarget}
            canSave={canSave(state)}
            disabled={busy}
            onEdit={(key, value) => dispatch({ type: 'edit', key, value })}
            onPointUdp={() => {
              if (udpTarget?.ip) dispatch({ type: 'point-udp', ip: udpTarget.ip, port: udpTarget.port })
            }}
            onRepairBom={() => dispatch({ type: 'repair-bom' })}
            onToggleAdvanced={() => dispatch({ type: 'advanced-toggle' })}
            onAck={(ack) => dispatch({ type: 'advanced-ack', ack })}
            onSave={() => void onSave()}
          />

          <LogList
            rows={state.logs.entries}
            selected={state.logs.selected}
            items={state.transfer.items}
            run={state.transfer.run}
            disabled={state.transfer.running}
            onSelect={(name, selected) => dispatch({ type: 'select', name, selected })}
            onSelectAll={(selected) => dispatch({ type: 'select-all', selected })}
          />

          <TransferPanel
            dest={state.dest}
            transfer={state.transfer}
            canStart={canStart(state)}
            onPickDest={() => void onPickDest()}
            onReconnectDest={() => void onReconnectDest()}
            onKeep={(keep) => dispatch({ type: 'keep-copies', keep })}
            onStart={() => void onStart()}
            onCancel={onCancel}
          />
        </>
      )}
    </div>
  )
}
