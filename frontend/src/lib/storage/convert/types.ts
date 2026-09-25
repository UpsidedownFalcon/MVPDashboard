// Contracts between the change-set 2 modules (agent-docs/03_PLAN_csv_summary
// section 5): the decoder (convert.ts) writes the CSV and meta.json and feeds
// every sample to a statistics sink; the pipeline (pipeline.ts) adds the
// summary; the worker (workers/convert.worker.ts) drives the pipeline over
// File System Access handles. Pure types plus two tiny helpers, no I/O.
import type { FileHeader } from '../binFormat'

/** One valid TIME_SYNC block: esp_timer and Unix microseconds at the same
 *  instant (u64 on the card, kept as bigint until a < 2^53 guard). */
export interface SyncAnchor {
  espUs: bigint
  unixUs: bigint
}

/** `<stem>.meta.json`: bin2csv.py's keys in bin2csv.py's order, then the
 *  dashboard extras (decision N: compared parsed, never byte-wise). */
export interface MetaJson {
  fw: string
  device_id: number
  source_id: number
  sensor_count: number
  odr_hz: number
  accel_fs_g: number
  gyro_fs_dps: number
  /** The header's float32 scales widened to double, as Python reads them. */
  accel_scale: number
  gyro_scale: number
  session_id: number
  /** Name of the raw file on the PC (LOG_0010.BIN, or LOG_0010-2.BIN for a dup). */
  source_file: string
  units: 'physical'
  /** Whole blocks with a good magic and CRC (any type). */
  blocks_valid: number
  /** Whole blocks that are not valid. bin2csv.py counts a preallocated
   *  0xFF / 0x00 tail here too, so this is scanner bad + scanner unused. */
  blocks_bad: number
  seq_gaps: number
  fifo_overflows: number
  clean_end: boolean
  time_sync: { esp_us: number; unix_us: number }[]
  /** Rows written per sensor id, keys as decimal strings ("1", "2"). */
  rows: Record<string, number>
  /** Dashboard extras. */
  unused_tail_blocks: number
  /** Index (0 = first block after the header) of the first block that is
   *  neither valid nor an unused 0xFF / 0x00 block; null when there is none. */
  first_bad: number | null
}

/** One string and one double per possible i16 count, index = count + 32768.
 *  The text is what bin2csv.py writes for that count (accel %.6f, gyro %.4f);
 *  the value is Number(text), i.e. what pandas reads back, which is what
 *  sensor_stats.py computes from. */
export interface ValueTables {
  accelText: string[]
  gyroText: string[]
  accelValue: Float64Array
  gyroValue: Float64Array
}

/** Consumer of every sample of every valid IMU block (sample_count <= 290),
 *  in file order, once per pass (decision T: light pre-pass + main pass).
 *  `tUs` = base_ts_us + dt_us as a Number (the decoder guards < 2^53); the
 *  six raw i16 counts follow. Pass 1 exists so the sink knows the medians it
 *  needs before pass 2 (window length from median dt; the |a| median bin). */
export interface SampleSink {
  /** Called once, after the header parsed and before pass 1. */
  start(header: FileHeader, tables: ValueTables): void
  pass1(sensorId: number, tUs: number, ax: number, ay: number, az: number, gx: number, gy: number, gz: number): void
  /** Pass 1 is over: derive the medians and prepare pass 2. */
  pass1Done(): void
  /** The same samples again in the same order. */
  pass2(sensorId: number, tUs: number, ax: number, ay: number, az: number, gx: number, gy: number, gz: number): void
}

export type ConvertPhase = 'prepass' | 'convert'

export interface ConvertProgress {
  phase: ConvertPhase
  /** Bytes of the raw file consumed in this phase. */
  bytesDone: number
  bytesTotal: number
  /** CSV rows written so far (0 during the pre-pass). */
  rows: number
}

export type ConvertErrorCode =
  /** The 512 B header is not a format_version 1 NYKS header (bin2csv.py raises too). */
  | 'format'
  /** A u64 field does not fit a double's 53 bits. */
  | 'range'
  | 'read'
  | 'write'
  | 'aborted'

export class ConvertError extends Error {
  constructor(
    readonly code: ConvertErrorCode,
    message: string,
  ) {
    super(message)
    this.name = 'ConvertError'
  }
}

export interface ConvertOptions {
  /** Output stem: the raw file name without its extension (LOG_0010, LOG_0010-2). */
  stem: string
  /** meta.source_file: the raw file's name on the PC. */
  sourceFile: string
  /** Card-style read budget (lib/storage/io.ts chunkPlan). */
  readChunkBytes: number
  /** CSV encode buffer; flushed to the writable when full. */
  writeChunkBytes: number
  signal?: AbortSignal
  onProgress?: (p: ConvertProgress) => void
  sink?: SampleSink
}

export interface ConvertResult {
  header: FileHeader
  meta: MetaJson
  syncs: SyncAnchor[]
  tables: ValueTables
  outputs: OutputNames
}

export interface OutputNames {
  csv: string
  meta: string
  summary: string
}

/** The three outputs next to raw/ (decisions K and M). */
export function outputNames(stem: string): OutputNames {
  return { csv: `${stem}.csv`, meta: `${stem}.meta.json`, summary: `${stem}_summary.txt` }
}

/** LOG_0010.BIN -> LOG_0010; LOG_0010-2.BIN -> LOG_0010-2. */
export function stemOf(rawName: string): string {
  const dot = rawName.lastIndexOf('.')
  return dot > 0 ? rawName.slice(0, dot) : rawName
}

export interface PipelineOptions extends Omit<ConvertOptions, 'sink'> {
  /** Placement text for the summary's placement column (29 chars wide):
   *  sensor 1 is the thigh, 2 the shin, side from the dashboard when known. */
  placement: (sensorId: number) => string
  fCutHz: number
  gapUs: number
  tsOutlierUs: number
}

export interface PipelineResult {
  meta: MetaJson
  /** The text written to `<stem>_summary.txt`, also shown in the page. */
  summary: string
  outputs: OutputNames
}
