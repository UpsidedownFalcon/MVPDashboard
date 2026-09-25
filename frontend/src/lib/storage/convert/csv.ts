// The CSV byte stream (agent-docs/03_PLAN_csv_summary 4.3): row text is
// encoded straight into a buffer of `writeChunkBytes` with
// TextEncoder.encodeInto and the buffer is handed to the ByteSink whenever
// it fills, so a 9 GB CSV never exists in memory. bin2csv.py's header line
// lives here. A row is at most 127 ASCII bytes (3 + 3 + 10 digit ids and
// seq, two 16-digit timestamps, six values no wider than -4000.0000, ten
// commas, LF), so `row()` is synchronous unless it had to flush first: it
// returns a promise only then, which spares the 31 M rows of a 512 MiB log
// an `await` each. Sink failures surface as ConvertError('write').
import { errorDetail, type ByteSink } from '../io'
import { ConvertError } from './types'

export const CSV_HEADER = 'device_id,sensor_id,seq,t_us,unix_us,ax,ay,az,gx,gy,gz\n'

/** Room kept free before a row is encoded; above the longest possible row
 *  (127 bytes), so a row never splits across two encodeInto calls. */
export const MAX_ROW_BYTES = 160

const ENCODER = new TextEncoder()

export class CsvWriter {
  /** Rows appended (the header line excluded). */
  rows = 0
  /** Bytes handed to the sink so far. */
  bytesWritten = 0
  private readonly buffer: Uint8Array
  private used = 0
  private closed = false
  /** Set by the first sink failure: the writer is dead from then on (only
   *  abort() is meaningful), so a later close() can never commit a file
   *  with rows missing. */
  private failed: ConvertError | null = null

  /** The buffer is at least MAX_ROW_BYTES whatever `writeChunkBytes` says,
   *  so a tiny budget degrades to one sink write per row, never to a stall. */
  constructor(
    private readonly sink: ByteSink,
    writeChunkBytes: number,
  ) {
    this.buffer = new Uint8Array(Math.max(writeChunkBytes, MAX_ROW_BYTES))
  }

  /** bin2csv.py's column header. */
  header(): Promise<void> | undefined {
    return this.put(CSV_HEADER)
  }

  /** Append one row (the text includes its LF). Await the result when it
   *  is a promise; it is undefined when the row fitted without a flush. */
  row(text: string): Promise<void> | undefined {
    this.rows++
    return this.put(text)
  }

  /** Hand the buffered bytes to the sink as a fresh copy (a sink may keep
   *  the array it is given; the buffer is reused immediately). */
  async flush(): Promise<void> {
    if (this.failed) throw this.failed
    if (this.used === 0) return
    const out = this.buffer.slice(0, this.used)
    this.used = 0
    try {
      await this.sink.write(out)
    } catch (err) {
      throw this.fail(err)
    }
    this.bytesWritten += out.length
  }

  /** Flush and commit. */
  async close(): Promise<void> {
    await this.flush()
    try {
      await this.sink.close()
    } catch (err) {
      throw this.fail(err)
    }
    this.closed = true
  }

  /** Discard: nothing reaches the destination. A no-op once close()
   *  succeeded; never throws. */
  async abort(): Promise<void> {
    this.used = 0
    if (this.closed) return
    this.closed = true
    await this.sink.abort().catch(() => undefined)
  }

  private fail(err: unknown): ConvertError {
    this.failed = new ConvertError('write', errorDetail(err))
    return this.failed
  }

  private put(text: string): Promise<void> | undefined {
    if (this.buffer.length - this.used >= MAX_ROW_BYTES) {
      const r = ENCODER.encodeInto(text, this.buffer.subarray(this.used))
      this.used += r.written
      if (r.read === text.length) return undefined
      // Longer than the room left (never a CSV row): finish it in pieces.
      return this.putSlow(text.slice(r.read))
    }
    return this.putSlow(text)
  }

  private async putSlow(text: string): Promise<void> {
    let rest = text
    while (rest.length > 0) {
      await this.flush()
      const r = ENCODER.encodeInto(rest, this.buffer)
      this.used = r.written
      rest = rest.slice(r.read)
    }
  }
}
