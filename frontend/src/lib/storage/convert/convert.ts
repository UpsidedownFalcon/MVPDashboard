// The decoder (agent-docs/03_PLAN_csv_summary 4.3): LOG_NNNN.BIN ->
// `<stem>.csv` + `<stem>.meta.json`, byte-exact with
// scripts/kneesleeve/bin2csv.py (decision N), in two streaming passes over
// the raw file (decision T) that also hand every sample to an optional
// SampleSink. Pass 1 ('prepass') is bin2csv's scan_syncs() plus the sink's
// first look at the samples; pass 2 ('convert') is bin2csv's convert() loop
// in the same order with the same counters. Bounded memory: one read chunk,
// one CSV buffer, the value tables. Nothing on the PC is ever deleted: an
// error or an abort only aborts the writables still open (a Chromium
// .crswap is discarded, MemDir keeps nothing).
import {
  BLOCK_TYPE_IMU,
  BLOCK_TYPE_SESSION_END,
  BLOCK_TYPE_TIME_SYNC,
  classifyBlock,
  FH_OFF_CRC,
  FH_OFF_FORMAT_VERSION,
  FH_OFF_HEADER_SIZE,
  FH_OFF_MAGIC,
  FILE_HEADER_BYTES,
  FLAG_FIFO_OVERFLOW,
  MAX_SAMPLES,
  parseFileHeader,
  readBlockHeader,
  SAMPLE_AREA_OFFSET,
  SAMPLE_BYTES,
  SAMPLE_OFF_AX,
  SAMPLE_OFF_AY,
  SAMPLE_OFF_AZ,
  SAMPLE_OFF_DT_US,
  SAMPLE_OFF_GX,
  SAMPLE_OFF_GY,
  SAMPLE_OFF_GZ,
  viewOf,
  type BlockHeader,
  type FileHeader,
  type FileHeaderErrorCode,
} from '../binFormat'
import { crc32Of } from '../crc32'
import { errorDetail, type ByteSink, type ByteSource, type DirLike } from '../io'
import { logFrames } from './blocks'
import { CsvWriter } from './csv'
import { buildMeta, metaText, type IntegrityCounts } from './meta'
import { buildValueTables, TABLE_OFFSET } from './scaled'
import { collectSync, pickSync, sortSyncs } from './syncs'
import {
  ConvertError,
  outputNames,
  type ConvertErrorCode,
  type ConvertOptions,
  type ConvertResult,
  type SampleSink,
  type SyncAnchor,
  type ValueTables,
} from './types'

/** log_sample_t.dt_us is a u16: the largest offset a sample can add to its
 *  block's base_ts_us. */
const DT_US_MAX = 0xffff

/** Abort wins; a ConvertError keeps its code; anything else is I/O of the
 *  given kind (the only foreign code that runs in each region). */
function asConvertError(err: unknown, code: ConvertErrorCode, signal?: AbortSignal): ConvertError {
  if (signal?.aborted) return new ConvertError('aborted', errorDetail(err))
  if (err instanceof ConvertError) return err
  return new ConvertError(code, errorDetail(err))
}

async function attempt<T>(op: () => Promise<T>, code: ConvertErrorCode, signal?: AbortSignal): Promise<T> {
  try {
    return await op()
  } catch (err) {
    throw asConvertError(err, code, signal)
  }
}

function hex8(n: number): string {
  return n.toString(16).toUpperCase().padStart(8, '0')
}

/** bin2csv.parse_file_header's ValueError texts. */
function headerMessage(code: FileHeaderErrorCode, bytes: Uint8Array): string {
  if (code === 'short') return `file shorter than ${FILE_HEADER_BYTES} B header`
  const v = viewOf(bytes)
  if (code === 'magic') return `bad file magic 0x${hex8(v.getUint32(FH_OFF_MAGIC, true))}`
  if (code === 'version') {
    return `unsupported format_version=${v.getUint16(FH_OFF_FORMAT_VERSION, true)} header_size=${v.getUint16(FH_OFF_HEADER_SIZE, true)}`
  }
  return `header CRC mismatch (stored ${hex8(v.getUint32(FH_OFF_CRC, true))}, calc ${hex8(crc32Of(bytes.subarray(0, FH_OFF_CRC)))})`
}

function parseHeaderOrThrow(bytes: Uint8Array): FileHeader {
  const parsed = parseFileHeader(bytes)
  if (!parsed.ok) throw new ConvertError('format', headerMessage(parsed.code, bytes))
  return parsed.header
}

/** base_ts_us as a Number, guarded once per block so that base + any dt_us
 *  is still an exact integer. */
function baseNumber(h: BlockHeader, index: number): number {
  const base = Number(h.baseTsUs)
  if (!Number.isSafeInteger(base + DT_US_MAX)) {
    throw new ConvertError('range', `block ${index}: base_ts_us ${h.baseTsUs} exceeds 2^53`)
  }
  return base
}

/** unix_us - esp_us of the picked anchor as a Number, with base + dt + off
 *  guaranteed exact for every dt_us of the block. */
function offsetNumber(pick: SyncAnchor, base: number, index: number): number {
  const diff = pick.unixUs - pick.espUs
  const off = Number(diff)
  if (!Number.isSafeInteger(off) || !Number.isSafeInteger(base + off) || !Number.isSafeInteger(base + DT_US_MAX + off)) {
    throw new ConvertError('range', `block ${index}: unix_us offset ${diff} exceeds 2^53`)
  }
  return off
}

interface Prepass {
  header: FileHeader
  tables: ValueTables
  anchors: SyncAnchor[]
}

/** bin2csv.scan_syncs() plus the sink's first pass. Samples are decoded
 *  only when a sink wants them; the anchors are collected regardless. */
async function prepass(src: ByteSource, opts: ConvertOptions): Promise<Prepass> {
  const { sink, signal } = opts
  const bytesTotal = src.size
  let header: FileHeader | null = null
  let tables: ValueTables | null = null
  const anchors: SyncAnchor[] = []
  try {
    for await (const f of logFrames(src, opts.readChunkBytes, signal)) {
      if (f.kind === 'chunk') {
        opts.onProgress?.({ phase: 'prepass', bytesDone: f.bytesDone, bytesTotal, rows: 0 })
        continue
      }
      if (f.kind === 'header') {
        header = parseHeaderOrThrow(f.bytes)
        tables = buildValueTables(header)
        sink?.start(header, tables)
        continue
      }
      if (classifyBlock(f.bytes) !== 'valid') continue
      const view = viewOf(f.bytes)
      const h = readBlockHeader(view, 0)
      if (h.type === BLOCK_TYPE_TIME_SYNC) {
        anchors.push(collectSync(f.bytes))
      } else if (h.type === BLOCK_TYPE_IMU && h.sampleCount <= MAX_SAMPLES && sink) {
        feedPass1(view, h, baseNumber(h, f.index), sink)
      }
    }
  } catch (err) {
    throw asConvertError(err, 'read', signal)
  }
  if (!header || !tables) throw new ConvertError('format', headerMessage('short', new Uint8Array(0)))
  return { header, tables, anchors }
}

function feedPass1(view: DataView, h: BlockHeader, base: number, sink: SampleSink): void {
  const sid = h.sensorId
  for (let i = 0, p = SAMPLE_AREA_OFFSET; i < h.sampleCount; i++, p += SAMPLE_BYTES) {
    sink.pass1(
      sid,
      base + view.getUint16(p + SAMPLE_OFF_DT_US, true),
      view.getInt16(p + SAMPLE_OFF_AX, true),
      view.getInt16(p + SAMPLE_OFF_AY, true),
      view.getInt16(p + SAMPLE_OFF_AZ, true),
      view.getInt16(p + SAMPLE_OFF_GX, true),
      view.getInt16(p + SAMPLE_OFF_GY, true),
      view.getInt16(p + SAMPLE_OFF_GZ, true),
    )
  }
}

interface MainPass {
  counts: IntegrityCounts
  rows: Map<number, number>
}

/** bin2csv.convert()'s block loop, verbatim in order and counters. */
async function mainPass(
  src: ByteSource,
  opts: ConvertOptions,
  header: FileHeader,
  tables: ValueTables,
  anchors: readonly SyncAnchor[],
  writer: CsvWriter,
): Promise<MainPass> {
  const { sink, signal } = opts
  const bytesTotal = src.size
  const { accelText, gyroText } = tables
  const counts: IntegrityCounts = {
    blocksValid: 0,
    blocksBad: 0,
    unusedTailBlocks: 0,
    firstBad: null,
    seqGaps: 0,
    fifoOverflows: 0,
    cleanEnd: false,
  }
  let lastSeq: number | null = null
  const rows = new Map<number, number>()
  let rowsWritten = 0
  const devPrefix = `${header.deviceId},`
  try {
    for await (const f of logFrames(src, opts.readChunkBytes, signal)) {
      if (f.kind === 'chunk') {
        opts.onProgress?.({ phase: 'convert', bytesDone: f.bytesDone, bytesTotal, rows: rowsWritten })
        continue
      }
      if (f.kind === 'header') continue
      const cls = classifyBlock(f.bytes)
      if (cls !== 'valid') {
        // bin2csv: bad magic or CRC, skipped; a 0xFF / 0x00 block is one of those.
        counts.blocksBad++
        if (cls === 'unused') counts.unusedTailBlocks++
        else if (counts.firstBad === null) counts.firstBad = f.index
        continue
      }
      counts.blocksValid++
      const view = viewOf(f.bytes)
      const h = readBlockHeader(view, 0)
      if (lastSeq !== null && h.seq !== lastSeq + 1) counts.seqGaps++
      lastSeq = h.seq
      if (h.type === BLOCK_TYPE_SESSION_END) {
        counts.cleanEnd = true
        continue
      }
      if (h.type !== BLOCK_TYPE_IMU) continue
      if (h.sampleCount > MAX_SAMPLES) continue
      if (h.flags & FLAG_FIFO_OVERFLOW) counts.fifoOverflows++
      const base = baseNumber(h, f.index)
      const synced = anchors.length > 0
      const off = synced ? offsetNumber(pickSync(anchors, h.baseTsUs), base, f.index) : 0
      const sid = h.sensorId
      const prefix = `${devPrefix}${sid},${h.seq},`
      for (let i = 0, p = SAMPLE_AREA_OFFSET; i < h.sampleCount; i++, p += SAMPLE_BYTES) {
        const t = base + view.getUint16(p + SAMPLE_OFF_DT_US, true)
        const ax = view.getInt16(p + SAMPLE_OFF_AX, true)
        const ay = view.getInt16(p + SAMPLE_OFF_AY, true)
        const az = view.getInt16(p + SAMPLE_OFF_AZ, true)
        const gx = view.getInt16(p + SAMPLE_OFF_GX, true)
        const gy = view.getInt16(p + SAMPLE_OFF_GY, true)
        const gz = view.getInt16(p + SAMPLE_OFF_GZ, true)
        const line =
          prefix +
          t +
          ',' +
          (synced ? t + off : '') +
          ',' +
          accelText[ax + TABLE_OFFSET] +
          ',' +
          accelText[ay + TABLE_OFFSET] +
          ',' +
          accelText[az + TABLE_OFFSET] +
          ',' +
          gyroText[gx + TABLE_OFFSET] +
          ',' +
          gyroText[gy + TABLE_OFFSET] +
          ',' +
          gyroText[gz + TABLE_OFFSET] +
          '\n'
        const pending = writer.row(line)
        if (pending) await pending
        sink?.pass2(sid, t, ax, ay, az, gx, gy, gz)
      }
      rows.set(sid, (rows.get(sid) ?? 0) + h.sampleCount)
      rowsWritten += h.sampleCount
    }
  } catch (err) {
    throw asConvertError(err, 'read', signal)
  }
  return { counts, rows }
}

/** Decode `src` into `out` as `<stem>.csv` and `<stem>.meta.json`. Rejects
 *  with a ConvertError only; on any failure the outputs are aborted, never
 *  left partial, and `out` is otherwise untouched. */
export async function convertLog(src: ByteSource, out: DirLike, opts: ConvertOptions): Promise<ConvertResult> {
  const { signal } = opts
  const outputs = outputNames(opts.stem)
  let writer: CsvWriter | null = null
  let metaSink: ByteSink | null = null
  let metaOpen = false
  try {
    const pre = await prepass(src, opts)
    const syncs = sortSyncs(pre.anchors)
    opts.sink?.pass1Done()

    writer = new CsvWriter(await attempt(() => out.create(outputs.csv), 'write', signal), opts.writeChunkBytes)
    const pendingHeader = writer.header()
    if (pendingHeader) await pendingHeader
    const main = await mainPass(src, opts, pre.header, pre.tables, syncs, writer)
    await attempt(() => writer!.close(), 'write', signal)

    const meta = buildMeta(pre.header, opts.sourceFile, main.counts, syncs, main.rows)
    const metaBytes = new TextEncoder().encode(metaText(meta))
    metaSink = await attempt(() => out.create(outputs.meta), 'write', signal)
    metaOpen = true
    const sink = metaSink
    await attempt(() => sink.write(metaBytes), 'write', signal)
    await attempt(() => sink.close(), 'write', signal)
    metaOpen = false
    return { header: pre.header, meta, syncs, tables: pre.tables, outputs }
  } catch (err) {
    throw asConvertError(err, 'write', signal)
  } finally {
    // A no-op once the CSV closed cleanly; otherwise the writable (and its
    // .crswap) must never be left dangling in the destination.
    await writer?.abort()
    if (metaOpen) await metaSink?.abort().catch(() => undefined)
  }
}
