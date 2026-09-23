// Boundary-agnostic LOG_NNNN.BIN scanner: feed it the file in chunks of any
// size and it classifies every 4096 B block (valid / bad / unused) after the
// 512 B header, mirroring bin2csv.py's integrity pass. It never stops early:
// a bad block is counted (firstBad remembered) and scanning continues; a
// SESSION_END block sets cleanEnd and scanning continues. Shared by the
// transfer verify (equal results on the card read and the local re-read)
// and CS2's decoder.
import { STORAGE_BAD_BLOCK_CONFIRM_MAX } from '../config'
import {
  BLOCK_BYTES,
  BLOCK_TYPE_IMU,
  BLOCK_TYPE_SESSION_END,
  BLOCK_TYPE_TIME_SYNC,
  classifyBlock,
  FILE_HEADER_BYTES,
  FLAG_FIFO_OVERFLOW,
  FLAG_TS_CLAMPED,
  MAX_SAMPLES,
  parseFileHeader,
  readBlockHeader,
  SYNC_OFF_ESP_US,
  SYNC_OFF_SOURCE,
  SYNC_OFF_UNIX_US,
  viewOf,
  type FileHeader,
  type FileHeaderErrorCode,
} from './binFormat'

export interface SyncBlock {
  espUs: bigint
  unixUs: bigint
  source: number
}

export interface ScanResult {
  header: FileHeader | null
  headerError?: FileHeaderErrorCode
  /** Whole blocks seen (valid + bad + unused). */
  blocks: number
  valid: number
  bad: number
  unused: number
  /** Index of the first bad block, or null. */
  firstBad: number | null
  /** Indices of bad blocks in file order, the first
   *  STORAGE_BAD_BLOCK_CONFIRM_MAX only (transfer re-reads them from the card
   *  before deleting; a transient read error must never look like on-card
   *  corruption). */
  badBlocks: number[]
  /** Index of the first unused (all-0x00 / all-0xFF) block, or null. */
  firstUnused: number | null
  /** Bytes after the last whole block (a partial block), or, when the file
   *  is shorter than a header, all of its bytes. */
  trailingBytes: number
  /** seq discontinuities between consecutive VALID blocks of any type. */
  seqGaps: number
  cleanEnd: boolean
  syncBlocks: SyncBlock[]
  /** Samples per sensor id in decodable IMU blocks (sample_count <= 290). */
  samples: { 1: number; 2: number; [sensorId: number]: number }
  fifoOverflows: number
  tsClamped: number
  lastSeq: number | null
}

export class BlockScanner {
  private readonly headerBuf = new Uint8Array(FILE_HEADER_BYTES)
  private headerLen = 0
  private header: FileHeader | null = null
  private headerError: FileHeaderErrorCode | undefined
  private readonly carry = new Uint8Array(BLOCK_BYTES)
  private carryLen = 0
  private blocks = 0
  private valid = 0
  private bad = 0
  private unused = 0
  private firstBad: number | null = null
  private readonly badBlocks: number[] = []
  private firstUnused: number | null = null
  private seqGaps = 0
  private cleanEnd = false
  private readonly syncBlocks: SyncBlock[] = []
  private readonly samples: ScanResult['samples'] = { 1: 0, 2: 0 }
  private fifoOverflows = 0
  private tsClamped = 0
  private lastSeq: number | null = null
  private result: ScanResult | null = null

  push(chunk: Uint8Array): void {
    if (this.result) throw new Error('BlockScanner: push after finish')
    let off = 0
    if (this.headerLen < FILE_HEADER_BYTES) {
      const n = Math.min(FILE_HEADER_BYTES - this.headerLen, chunk.length)
      this.headerBuf.set(chunk.subarray(0, n), this.headerLen)
      this.headerLen += n
      off = n
      if (this.headerLen === FILE_HEADER_BYTES) {
        const parsed = parseFileHeader(this.headerBuf)
        if (parsed.ok) this.header = parsed.header
        else this.headerError = parsed.code
      }
      if (off >= chunk.length) return
    }
    if (this.carryLen > 0) {
      const n = Math.min(BLOCK_BYTES - this.carryLen, chunk.length - off)
      this.carry.set(chunk.subarray(off, off + n), this.carryLen)
      this.carryLen += n
      off += n
      if (this.carryLen < BLOCK_BYTES) return
      this.block(this.carry)
      this.carryLen = 0
    }
    while (chunk.length - off >= BLOCK_BYTES) {
      this.block(chunk.subarray(off, off + BLOCK_BYTES))
      off += BLOCK_BYTES
    }
    if (off < chunk.length) {
      this.carry.set(chunk.subarray(off), 0)
      this.carryLen = chunk.length - off
    }
  }

  finish(): ScanResult {
    if (this.result) return this.result
    const shortFile = this.headerLen < FILE_HEADER_BYTES
    this.result = {
      header: this.header,
      headerError: shortFile ? 'short' : this.headerError,
      blocks: this.blocks,
      valid: this.valid,
      bad: this.bad,
      unused: this.unused,
      firstBad: this.firstBad,
      badBlocks: this.badBlocks,
      firstUnused: this.firstUnused,
      trailingBytes: shortFile ? this.headerLen : this.carryLen,
      seqGaps: this.seqGaps,
      cleanEnd: this.cleanEnd,
      syncBlocks: this.syncBlocks,
      samples: this.samples,
      fifoOverflows: this.fifoOverflows,
      tsClamped: this.tsClamped,
      lastSeq: this.lastSeq,
    }
    return this.result
  }

  private block(block: Uint8Array): void {
    const index = this.blocks++
    const cls = classifyBlock(block)
    if (cls === 'unused') {
      this.unused++
      if (this.firstUnused === null) this.firstUnused = index
      return
    }
    if (cls === 'bad') {
      this.bad++
      if (this.firstBad === null) this.firstBad = index
      if (this.badBlocks.length < STORAGE_BAD_BLOCK_CONFIRM_MAX) this.badBlocks.push(index)
      return
    }
    this.valid++
    const view = viewOf(block)
    const h = readBlockHeader(view, 0)
    if (this.lastSeq !== null && h.seq !== this.lastSeq + 1) this.seqGaps++
    this.lastSeq = h.seq
    if (h.type === BLOCK_TYPE_SESSION_END) {
      this.cleanEnd = true
      return
    }
    if (h.type === BLOCK_TYPE_TIME_SYNC) {
      this.syncBlocks.push({
        espUs: view.getBigUint64(SYNC_OFF_ESP_US, true),
        unixUs: view.getBigUint64(SYNC_OFF_UNIX_US, true),
        source: view.getUint8(SYNC_OFF_SOURCE),
      })
      return
    }
    if (h.type !== BLOCK_TYPE_IMU) return
    // bin2csv skips the samples of an over-full block but still counts it valid.
    if (h.sampleCount > MAX_SAMPLES) return
    if (h.flags & FLAG_FIFO_OVERFLOW) this.fifoOverflows++
    if (h.flags & FLAG_TS_CLAMPED) this.tsClamped++
    this.samples[h.sensorId] = (this.samples[h.sensorId] ?? 0) + h.sampleCount
  }
}

/** Scan a whole in-memory file in one go. */
export function scanBytes(bytes: Uint8Array): ScanResult {
  const s = new BlockScanner()
  s.push(bytes)
  return s.finish()
}

function headersEqual(a: FileHeader | null, b: FileHeader | null): boolean {
  if (a === null || b === null) return a === b
  return (
    a.formatVersion === b.formatVersion &&
    a.headerSize === b.headerSize &&
    a.fw === b.fw &&
    a.deviceId === b.deviceId &&
    a.sourceId === b.sourceId &&
    a.sensorCount === b.sensorCount &&
    a.fifoWatermark === b.fifoWatermark &&
    a.odrHz === b.odrHz &&
    a.accelFsG === b.accelFsG &&
    a.gyroFsDps === b.gyroFsDps &&
    a.accelScale === b.accelScale &&
    a.gyroScale === b.gyroScale &&
    a.sessionId === b.sessionId &&
    a.bootEspUs === b.bootEspUs &&
    a.utcValid === b.utcValid &&
    a.crc32 === b.crc32
  )
}

/** Deep equality of two scan results (every field). */
export function scanResultsEqual(a: ScanResult, b: ScanResult): boolean {
  if (!headersEqual(a.header, b.header)) return false
  if (a.headerError !== b.headerError) return false
  if (
    a.blocks !== b.blocks ||
    a.valid !== b.valid ||
    a.bad !== b.bad ||
    a.unused !== b.unused ||
    a.firstBad !== b.firstBad ||
    a.firstUnused !== b.firstUnused ||
    a.trailingBytes !== b.trailingBytes ||
    a.seqGaps !== b.seqGaps ||
    a.cleanEnd !== b.cleanEnd ||
    a.fifoOverflows !== b.fifoOverflows ||
    a.tsClamped !== b.tsClamped ||
    a.lastSeq !== b.lastSeq
  ) {
    return false
  }
  if (a.badBlocks.length !== b.badBlocks.length) return false
  for (let i = 0; i < a.badBlocks.length; i++) if (a.badBlocks[i] !== b.badBlocks[i]) return false
  if (a.syncBlocks.length !== b.syncBlocks.length) return false
  for (let i = 0; i < a.syncBlocks.length; i++) {
    const x = a.syncBlocks[i]
    const y = b.syncBlocks[i]
    if (x.espUs !== y.espUs || x.unixUs !== y.unixUs || x.source !== y.source) return false
  }
  const sensorIds = new Set([...Object.keys(a.samples), ...Object.keys(b.samples)])
  for (const id of sensorIds) {
    if ((a.samples[Number(id)] ?? 0) !== (b.samples[Number(id)] ?? 0)) return false
  }
  return true
}
