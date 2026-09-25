import { describe, expect, it } from 'vitest'
import { bytesEqual, type ByteSink } from '../io'
import { MemDir } from '../memDir'
import { CSV_HEADER, CsvWriter, MAX_ROW_BYTES } from './csv'
import { ConvertError } from './types'

const encoder = new TextEncoder()
const ROWS = [
  '7,1,0,1000,,-0.677734,0.398438,0.617188,0.9766,5.8594,1.9531\n',
  '7,1,0,1156,1700000000001156,0.007812,0.023438,-0.007812,3.9062,11.7188,-3.9062\n',
  '7,2,1,1000,,0.000000,0.000977,-0.000977,0.0000,0.1221,-0.1221\n',
]

/** A sink that records every write as handed over (no copy), so a reused
 *  buffer would show up as corrupted earlier writes. */
class RecordingSink implements ByteSink {
  readonly writes: Uint8Array[] = []
  closed = false
  aborted = false
  async write(bytes: Uint8Array): Promise<void> {
    this.writes.push(bytes)
  }
  async close(): Promise<void> {
    this.closed = true
  }
  async abort(): Promise<void> {
    this.aborted = true
  }
  text(): string {
    return this.writes.map((w) => new TextDecoder().decode(w)).join('')
  }
}

async function writeAll(writer: CsvWriter, rows: string[]): Promise<void> {
  const h = writer.header()
  if (h) await h
  for (const r of rows) {
    const p = writer.row(r)
    if (p) await p
  }
  await writer.close()
}

describe('CsvWriter', () => {
  it('writes bin2csv.py\'s header and the rows byte for byte', async () => {
    const dir = new MemDir()
    const writer = new CsvWriter(await dir.create('x.csv'), 64 * 1024)
    await writeAll(writer, ROWS)
    expect(bytesEqual(dir.bytes('x.csv')!, encoder.encode(CSV_HEADER + ROWS.join('')))).toBe(true)
    expect(writer.rows).toBe(3)
    expect(writer.bytesWritten).toBe(CSV_HEADER.length + ROWS.join('').length)
    expect(dir.openSinks).toBe(0)
  })

  it('stays synchronous while the buffer has room and flushes when fewer than MAX_ROW_BYTES remain', async () => {
    const sink = new RecordingSink()
    const writer = new CsvWriter(sink, 200)
    expect(writer.header()).toBeUndefined() // 56 bytes in, 144 left (< MAX_ROW_BYTES)
    const p = writer.row(ROWS[0]) // must flush the header first
    expect(p).toBeInstanceOf(Promise)
    await p
    expect(sink.writes).toHaveLength(1)
    expect(new TextDecoder().decode(sink.writes[0])).toBe(CSV_HEADER)
    await writer.close()
    expect(sink.text()).toBe(CSV_HEADER + ROWS[0])
    expect(sink.closed).toBe(true)
  })

  it('a tiny writeChunkBytes degrades to one write per row and still concatenates exactly', async () => {
    const sink = new RecordingSink()
    const writer = new CsvWriter(sink, 1)
    await writeAll(writer, ROWS)
    expect(sink.writes.map((w) => new TextDecoder().decode(w))).toEqual([CSV_HEADER, ...ROWS])
    expect(sink.text()).toBe(CSV_HEADER + ROWS.join(''))
  })

  it('hands the sink a copy, never the reused buffer', async () => {
    const sink = new RecordingSink()
    const writer = new CsvWriter(sink, MAX_ROW_BYTES)
    await writeAll(writer, ROWS)
    expect(sink.writes.length).toBeGreaterThan(1)
    expect(new Set(sink.writes.map((w) => w.buffer)).size).toBe(sink.writes.length)
    expect(sink.text()).toBe(CSV_HEADER + ROWS.join(''))
  })

  it('fills each chunk to within a row of writeChunkBytes on a long stream', async () => {
    const sink = new RecordingSink()
    const CHUNK = 4096
    const writer = new CsvWriter(sink, CHUNK)
    const rows: string[] = []
    for (let i = 0; i < 500; i++) rows.push(ROWS[i % ROWS.length])
    await writeAll(writer, rows)
    expect(sink.text()).toBe(CSV_HEADER + rows.join(''))
    for (const w of sink.writes.slice(0, -1)) {
      expect(w.length).toBeLessThanOrEqual(CHUNK)
      expect(w.length).toBeGreaterThan(CHUNK - MAX_ROW_BYTES)
    }
    expect(writer.rows).toBe(500)
  })

  it('a text longer than the buffer is still written whole (slow path)', async () => {
    const sink = new RecordingSink()
    const writer = new CsvWriter(sink, 1)
    const long = 'x'.repeat(3 * MAX_ROW_BYTES + 7) + '\n'
    const p = writer.row(long)
    if (p) await p
    await writer.close()
    expect(sink.text()).toBe(long)
    expect(sink.writes.length).toBeGreaterThanOrEqual(3)
  })

  it('abort leaves nothing in the directory and closes no sink twice', async () => {
    const dir = new MemDir()
    const writer = new CsvWriter(await dir.create('x.csv'), 64 * 1024)
    await writeAll(writer, ROWS.slice(0, 1)).catch(() => undefined)
    // Reopen: this time abort mid-way.
    const second = new CsvWriter(await dir.create('y.csv'), 64 * 1024)
    second.header()
    const p = second.row(ROWS[0])
    if (p) await p
    await second.abort()
    expect(dir.files.has('y.csv')).toBe(false)
    expect(dir.files.has('x.csv')).toBe(true)
    expect(dir.openSinks).toBe(0)
    await second.abort() // idempotent
    await writer.abort() // no-op after a successful close
    expect(dir.files.has('x.csv')).toBe(true)
  })

  it('reports sink failures as ConvertError write and aborts cleanly afterwards', async () => {
    const dir = new MemDir().failWrite('x.csv')
    const writer = new CsvWriter(await dir.create('x.csv'), 1)
    const h = writer.header()
    expect(h).toBeUndefined()
    await expect(writer.close()).rejects.toMatchObject({ name: 'ConvertError', code: 'write' })
    // A failed writer stays failed: a second close() can never commit a
    // file with rows missing.
    await expect(writer.close()).rejects.toBeInstanceOf(ConvertError)
    await expect(writer.flush()).rejects.toMatchObject({ code: 'write' })
    expect(dir.files.has('x.csv')).toBe(false)
    await writer.abort()
    expect(dir.openSinks).toBe(0)
    expect(dir.files.has('x.csv')).toBe(false)

    const closeFails = new MemDir().failClose('z.csv')
    const w2 = new CsvWriter(await closeFails.create('z.csv'), 1024)
    w2.header()
    await expect(w2.close()).rejects.toMatchObject({ code: 'write' })
    expect((await w2.close().catch((e: ConvertError) => e.message)) as string).toContain('AbortError')
  })
})
