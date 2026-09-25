// `<stem>.meta.json`: bin2csv.py's sidecar (its keys in its order, from the
// header, the integrity counters, the sorted sync anchors and the rows per
// sensor) plus the two dashboard extras of agent-docs/03_PLAN_csv_summary
// 4.3. Text is JSON.stringify(meta, null, 1), the shape of Python's
// json.dumps(indent=1) with LF and no trailing newline; decision N compares
// meta parsed, so the platform newline and the order of the integer-like
// `rows` keys (which JavaScript sorts) do not matter.
import type { FileHeader } from '../binFormat'
import { ConvertError, type MetaJson, type SyncAnchor } from './types'

export interface IntegrityCounts {
  /** Whole blocks with a good magic and CRC. */
  blocksValid: number
  /** Scanner-bad + unused, bin2csv.py's blocks_bad. */
  blocksBad: number
  /** Whole blocks of all-0x00 / all-0xFF (counted in blocksBad too). */
  unusedTailBlocks: number
  firstBad: number | null
  seqGaps: number
  fifoOverflows: number
  cleanEnd: boolean
}

/** A u64 field as a JSON number; ConvertError('range') when it does not
 *  fit a double's 53 bits (bin2csv.py has Python's unbounded int). */
export function safeNumber(value: bigint, what: string): number {
  const n = Number(value)
  if (!Number.isSafeInteger(n)) throw new ConvertError('range', `${what} ${value} exceeds 2^53`)
  return n
}

export function buildMeta(
  header: FileHeader,
  sourceFile: string,
  counts: IntegrityCounts,
  syncs: readonly SyncAnchor[],
  rows: ReadonlyMap<number, number>,
): MetaJson {
  const rowsBySensor: Record<string, number> = {}
  for (const [sid, n] of rows) rowsBySensor[String(sid)] = n
  return {
    fw: header.fw,
    device_id: header.deviceId,
    source_id: header.sourceId,
    sensor_count: header.sensorCount,
    odr_hz: header.odrHz,
    accel_fs_g: header.accelFsG,
    gyro_fs_dps: header.gyroFsDps,
    accel_scale: header.accelScale,
    gyro_scale: header.gyroScale,
    session_id: header.sessionId,
    source_file: sourceFile,
    units: 'physical',
    blocks_valid: counts.blocksValid,
    blocks_bad: counts.blocksBad,
    seq_gaps: counts.seqGaps,
    fifo_overflows: counts.fifoOverflows,
    clean_end: counts.cleanEnd,
    time_sync: syncs.map((s, i) => ({
      esp_us: safeNumber(s.espUs, `time_sync[${i}].esp_us`),
      unix_us: safeNumber(s.unixUs, `time_sync[${i}].unix_us`),
    })),
    rows: rowsBySensor,
    unused_tail_blocks: counts.unusedTailBlocks,
    first_bad: counts.firstBad,
  }
}

/** The file text: indent 1, LF, no trailing newline. */
export function metaText(meta: MetaJson): string {
  return JSON.stringify(meta, null, 1)
}
