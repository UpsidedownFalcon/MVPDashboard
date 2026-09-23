// The verified-transfer engine (PLAN_msd_management 4.3). Pure: talks to two
// DirLike trees (the card and the destination) and yields events the page
// dispatches. Per item: probe -> copy -> verify -> dedupe -> delete, and the
// card is touched (deleted from) ONLY after the local copy verified, when
// copies are not being kept and the run was not aborted. Every failure
// leaves the card as it was; a failed or unverified local copy is removed.
// Runs on the main thread; no DOM dependency, so it could move to a worker.
import { STORAGE_EXPECTED_BYTES_PER_S, STORAGE_RATE_EWMA_ALPHA } from '../config'
import { crc32Final, crc32Init, crc32Update } from './crc32'
import { bytesEqual, concatBytes, errorDetail, errorName, readChunks, type ByteSource, type DirLike } from './io'
import { dupName, type LogKind } from './logNames'
import { BLOCK_BYTES, FILE_HEADER_BYTES } from './binFormat'
import { BlockScanner, scanResultsEqual, type ScanResult } from './scanner'

/** Decision K: `<dest>/<folder>/raw/<name>`. */
export const RAW_SUBDIR = 'raw'
/** dupName() suffixes start here: LOG_0010-2.BIN. */
export const FIRST_DUP_SUFFIX = 2
/** Give up looking for a free dupName() after this many. */
const MAX_DUP_SUFFIX = 1000

export type TransferPhase = 'probe' | 'copy' | 'verify' | 'dedupe' | 'delete'

export type TransferErrorCode =
  | 'permission'
  | 'card-read'
  | 'dest-write'
  | 'verify-mismatch'
  | 'card-delete'
  | 'aborted'

export type TransferResult = 'copied' | 'already-transferred'

export interface TransferItem {
  id: string
  /** Name on the card, e.g. LOG_0010.BIN. */
  name: string
  size: number
  kind: LogKind
  /** Destination folder under dest, e.g. sleeve-u1-0. */
  folder: string
}

export interface TransferDeps {
  card: DirLike
  dest: DirLike
  /** Decision J: true skips the delete phase for this run. */
  keepCopies: boolean
  /** Clock in ms (Date.now by default); injectable for tests. */
  now?: () => number
}

export interface TransferOptions {
  signal?: AbortSignal
  /** Read budget per chunk (STORAGE_READ_CHUNK_BYTES in the page). */
  chunkBytes: number
}

export interface TransferProgress {
  type: 'progress'
  id: string
  phase: TransferPhase
  /** Bytes of THIS item processed in this phase. */
  bytesDone: number
  bytesTotal: number
  /** EWMA of the card read rate (copy phase only feeds it). */
  bytesPerS: number
  /** Time to copy the bytes still unread from the card in this run. */
  etaMs: number
  runBytesDone: number
  runBytesTotal: number
}

export type TransferEvent =
  | { type: 'item-start'; id: string; name: string; bytesTotal: number }
  | TransferProgress
  | {
      type: 'item-done'
      id: string
      result: TransferResult
      deleted: boolean
      /** BIN items only. */
      scan?: ScanResult
      /** Name of the verified copy under `<folder>/raw/`. */
      localName: string
    }
  | {
      type: 'item-failed'
      id: string
      code: TransferErrorCode
      phase: TransferPhase
      detail?: string
      /** Set for 'card-delete': the verified copy that exists. */
      localName?: string
      scan?: ScanResult
    }
  | { type: 'done'; summary: TransferSummary }

export interface TransferSummary {
  items: number
  copied: number
  alreadyTransferred: number
  /** item-failed events (a 'card-delete' failure still left a verified copy). */
  failed: number
  deleted: number
  /** Items never started because the run was aborted. */
  notStarted: number
  aborted: boolean
  /** Bytes read from the card. */
  bytesCopied: number
  elapsedMs: number
}

class TransferFailure extends Error {
  constructor(
    readonly code: TransferErrorCode,
    readonly phase: TransferPhase,
    readonly detail?: string,
  ) {
    super(detail ? `${code}: ${detail}` : code)
    this.name = 'TransferFailure'
  }
}

type Side = 'card' | 'dest'

/** Map a thrown DOMException (or anything) to a transfer error code. */
function codeFor(err: unknown, side: Side, phase: TransferPhase, signal?: AbortSignal): TransferErrorCode {
  if (signal?.aborted) return 'aborted'
  const name = errorName(err)
  if (side === 'card') {
    if (phase === 'delete') return 'card-delete'
    return name === 'NotAllowedError' || name === 'SecurityError' ? 'permission' : 'card-read'
  }
  return 'dest-write'
}

function wrap(err: unknown, side: Side, phase: TransferPhase, signal?: AbortSignal): TransferFailure {
  if (err instanceof TransferFailure) return err
  return new TransferFailure(codeFor(err, side, phase, signal), phase, errorDetail(err))
}

async function attempt<T>(op: () => Promise<T>, side: Side, phase: TransferPhase, signal?: AbortSignal): Promise<T> {
  try {
    return await op()
  } catch (err) {
    throw wrap(err, side, phase, signal)
  }
}

function checkAbort(signal: AbortSignal | undefined, phase: TransferPhase): void {
  if (signal?.aborted) throw new TransferFailure('aborted', phase)
}

/** Exponentially weighted card read rate with an ETA for the bytes left. */
class RateMeter {
  private rate = STORAGE_EXPECTED_BYTES_PER_S
  private lastT: number
  private lastBytes = 0
  private total = 0

  constructor(
    private readonly now: () => number,
    startedAt: number,
  ) {
    this.lastT = startedAt
  }

  add(bytes: number): void {
    this.total += bytes
    const t = this.now()
    const dt = t - this.lastT
    if (dt <= 0) return
    const instant = ((this.total - this.lastBytes) * 1000) / dt
    this.rate = STORAGE_RATE_EWMA_ALPHA * instant + (1 - STORAGE_RATE_EWMA_ALPHA) * this.rate
    this.lastT = t
    this.lastBytes = this.total
  }

  get bytesPerS(): number {
    return this.rate
  }

  etaMs(remainingBytes: number): number {
    return this.rate > 0 ? Math.ceil((remainingBytes / this.rate) * 1000) : 0
  }
}

interface RunState {
  deps: TransferDeps
  opts: TransferOptions
  meter: RateMeter
  runBytesTotal: number
  runBytesDone: number
}

function progress(run: RunState, item: TransferItem, phase: TransferPhase, bytesDone: number): TransferProgress {
  return {
    type: 'progress',
    id: item.id,
    phase,
    bytesDone,
    bytesTotal: item.size,
    bytesPerS: run.meter.bytesPerS,
    etaMs: run.meter.etaMs(run.runBytesTotal - run.runBytesDone),
    runBytesDone: run.runBytesDone,
    runBytesTotal: run.runBytesTotal,
  }
}

interface ItemOutcome {
  result?: TransferResult
  failedCode?: TransferErrorCode
  deleted: boolean
  bytesRead: number
}

/** CRC32 of a source, yielding progress; errors map to `side`. */
async function* hashSource(
  src: ByteSource,
  side: Side,
  phase: TransferPhase,
  item: TransferItem,
  run: RunState,
): AsyncGenerator<TransferEvent, { crc: number; bytes: number; scan?: ScanResult }> {
  let state = crc32Init()
  let bytes = 0
  const scanner = item.kind === 'BIN' ? new BlockScanner() : null
  try {
    for await (const chunk of readChunks(src, run.opts.chunkBytes, run.opts.signal)) {
      state = crc32Update(state, chunk)
      scanner?.push(chunk)
      bytes += chunk.length
      yield progress(run, item, phase, bytes)
    }
  } catch (err) {
    throw wrap(err, side, phase, run.opts.signal)
  }
  return { crc: crc32Final(state), bytes, scan: scanner?.finish() }
}

async function readAll(src: ByteSource, side: Side, phase: TransferPhase, run: RunState): Promise<Uint8Array> {
  const parts: Uint8Array[] = []
  try {
    for await (const chunk of readChunks(src, run.opts.chunkBytes, run.opts.signal)) parts.push(chunk)
  } catch (err) {
    throw wrap(err, side, phase, run.opts.signal)
  }
  return concatBytes(parts)
}

async function freeName(dir: DirLike, name: string, phase: TransferPhase, signal?: AbortSignal): Promise<string> {
  for (let n = FIRST_DUP_SUFFIX; n < FIRST_DUP_SUFFIX + MAX_DUP_SUFFIX; n++) {
    const candidate = dupName(name, n)
    if (!(await attempt(() => dir.exists(candidate), 'dest', phase, signal))) return candidate
  }
  throw new TransferFailure('dest-write', phase, `no free name for ${name}`)
}

/** Defence in depth before the irreversible delete: every block the scan
 *  called bad is re-read from the card and must equal the local copy byte for
 *  byte, so a transient read error on the USB or SD path can never pass as
 *  on-card corruption and cost the only good copy. The scanner keeps at most
 *  STORAGE_BAD_BLOCK_CONFIRM_MAX indices, which bounds the extra reads. */
async function confirmBadBlocks(
  item: TransferItem,
  badBlocks: number[],
  local: ByteSource,
  run: RunState,
): Promise<string | null> {
  const { signal } = run.opts
  const again = await attempt(() => run.deps.card.open(item.name), 'card', 'verify', signal)
  for (const index of badBlocks) {
    const off = FILE_HEADER_BYTES + index * BLOCK_BYTES
    const onCard = await attempt(() => again.read(off, BLOCK_BYTES), 'card', 'verify', signal)
    const onDisk = await attempt(() => local.read(off, BLOCK_BYTES), 'dest', 'verify', signal)
    if (!bytesEqual(onCard, onDisk)) return `bad block ${index} re-read from the card differs`
  }
  return null
}

async function* transferItem(item: TransferItem, run: RunState): AsyncGenerator<TransferEvent, ItemOutcome> {
  const { deps, opts } = run
  const signal = opts.signal
  yield { type: 'item-start', id: item.id, name: item.name, bytesTotal: item.size }

  let phase: TransferPhase = 'probe'
  let rawDir: DirLike | null = null
  let localName = item.name
  let written = false
  let verified = false
  let bytesRead = 0
  let scan: ScanResult | undefined

  try {
    // ---- probe: destination folder, pre-existing same-name file
    rawDir = await attempt(
      async () => (await deps.dest.subdir(item.folder, true)).subdir(RAW_SUBDIR, true),
      'dest',
      phase,
      signal,
    )
    const dir = rawDir
    let prior: { size: number; crc: number | null } | null = null
    if (await attempt(() => dir.exists(item.name), 'dest', phase, signal)) {
      const existing = await attempt(() => dir.open(item.name), 'dest', phase, signal)
      prior = { size: existing.size, crc: null }
      if (existing.size === item.size) prior.crc = (yield* hashSource(existing, 'dest', phase, item, run)).crc
      localName = await freeName(dir, item.name, phase, signal)
    }
    checkAbort(signal, phase)

    // ---- copy: card -> <folder>/raw/<localName>, hashing and scanning as we go
    phase = 'copy'
    const src = await attempt(() => deps.card.open(item.name), 'card', phase, signal)
    const sink = await attempt(() => dir.create(localName), 'dest', phase, signal)
    let state = crc32Init()
    const scanner = item.kind === 'BIN' ? new BlockScanner() : null
    let sinkOpen = true
    try {
      for await (const chunk of readChunks(src, opts.chunkBytes, signal)) {
        state = crc32Update(state, chunk)
        scanner?.push(chunk)
        await attempt(() => sink.write(chunk), 'dest', phase, signal)
        bytesRead += chunk.length
        run.runBytesDone += chunk.length
        run.meter.add(chunk.length)
        yield progress(run, item, phase, bytesRead)
      }
      if (bytesRead !== item.size) {
        throw new TransferFailure('card-read', phase, `short read: ${bytesRead} of ${item.size} bytes`)
      }
      await attempt(() => sink.close(), 'dest', phase, signal)
      sinkOpen = false
    } catch (err) {
      throw wrap(err, 'card', phase, signal)
    } finally {
      // Runs on a failure AND when the consumer stops iterating (the page
      // unmounts and generator.return() unwinds us at a yield): a writable and
      // its .crswap must never be left dangling in the destination.
      if (sinkOpen) await sink.abort().catch(() => undefined)
    }
    written = true
    const cardCrc = crc32Final(state)
    scan = scanner?.finish()

    // ---- verify: re-read the LOCAL copy
    phase = 'verify'
    checkAbort(signal, phase)
    const local = await attempt(() => dir.open(localName), 'dest', phase, signal)
    let mismatch: string | null = null
    if (local.size !== item.size) {
      mismatch = `length ${local.size} != ${item.size}`
    } else if (item.kind === 'BIN') {
      const back = yield* hashSource(local, 'dest', phase, item, run)
      if (back.crc !== cardCrc) mismatch = 'crc32 differs'
      else if (scan && back.scan && !scanResultsEqual(back.scan, scan)) mismatch = 'block scan differs'
      else if (scan && scan.badBlocks.length > 0) mismatch = await confirmBadBlocks(item, scan.badBlocks, local, run)
    } else {
      const localBytes = await readAll(local, 'dest', phase, run)
      const again = await attempt(() => deps.card.open(item.name), 'card', phase, signal)
      const cardBytes = await readAll(again, 'card', phase, run)
      if (!bytesEqual(localBytes, cardBytes)) mismatch = 'bytes differ'
      yield progress(run, item, phase, localBytes.length)
    }
    if (mismatch) {
      await dir.remove(localName).catch(() => undefined)
      written = false
      throw new TransferFailure('verify-mismatch', phase, mismatch)
    }
    verified = true

    // ---- dedupe: an identical file was already there
    phase = 'dedupe'
    let result: TransferResult = 'copied'
    if (prior && prior.size === item.size && prior.crc === cardCrc) {
      try {
        await dir.remove(localName)
        localName = item.name
        result = 'already-transferred'
      } catch {
        // The duplicate stays; report it honestly as a copy under its dup name.
      }
    }

    // ---- delete: only now, only when asked, never after an abort
    phase = 'delete'
    let deleted = false
    if (!deps.keepCopies && !signal?.aborted) {
      try {
        await deps.card.remove(item.name)
        deleted = true
      } catch (err) {
        yield {
          type: 'item-failed',
          id: item.id,
          code: 'card-delete',
          phase,
          detail: errorDetail(err),
          localName,
          scan,
        }
        return { result, failedCode: 'card-delete', deleted: false, bytesRead }
      }
    }
    yield { type: 'item-done', id: item.id, result, deleted, scan, localName }
    return { result, deleted, bytesRead }
  } catch (err) {
    const failure = wrap(err, 'card', phase, signal)
    yield { type: 'item-failed', id: item.id, code: failure.code, phase: failure.phase, detail: failure.detail }
    return { failedCode: failure.code, deleted: false, bytesRead }
  } finally {
    // A copy that never verified must not linger, whether we failed above or
    // the consumer abandoned the run (generator.return()).
    if (written && !verified) await rawDir?.remove(localName).catch(() => undefined)
  }
}

/** Transfer `items` in order. Consume with `for await`; the final summary is
 *  both the last event and the generator's return value. An abort ends the
 *  run after the current item reports 'aborted'. */
export async function* runTransfer(
  items: TransferItem[],
  deps: TransferDeps,
  opts: TransferOptions,
): AsyncGenerator<TransferEvent, TransferSummary, undefined> {
  const now = deps.now ?? (() => Date.now())
  const startedAt = now()
  const run: RunState = {
    deps,
    opts,
    meter: new RateMeter(now, startedAt),
    runBytesTotal: items.reduce((sum, it) => sum + it.size, 0),
    runBytesDone: 0,
  }
  const summary: TransferSummary = {
    items: items.length,
    copied: 0,
    alreadyTransferred: 0,
    failed: 0,
    deleted: 0,
    notStarted: 0,
    aborted: false,
    bytesCopied: 0,
    elapsedMs: 0,
  }
  for (let i = 0; i < items.length; i++) {
    if (opts.signal?.aborted) {
      summary.aborted = true
      summary.notStarted = items.length - i
      break
    }
    const outcome = yield* transferItem(items[i], run)
    summary.bytesCopied += outcome.bytesRead
    if (outcome.deleted) summary.deleted++
    if (outcome.failedCode) summary.failed++
    else if (outcome.result === 'copied') summary.copied++
    else if (outcome.result === 'already-transferred') summary.alreadyTransferred++
    if (outcome.failedCode === 'aborted') {
      summary.aborted = true
      summary.notStarted = items.length - i - 1
      break
    }
  }
  summary.elapsedMs = now() - startedAt
  yield { type: 'done', summary }
  return summary
}
