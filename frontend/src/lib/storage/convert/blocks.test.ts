import { describe, expect, it } from 'vitest'
import { BLOCK_BYTES, FILE_HEADER_BYTES } from '../binFormat'
import { encodeLogFile, fill, rampSamples, type BlockSpec } from '../fixtures/binEncode'
import { bytesEqual, type ByteSource } from '../io'
import { MemDir } from '../memDir'
import { logFrames, type BlockFrame, type FramingEnd, type LogFrame } from './blocks'

const MIB = 1024 * 1024
const ONE_BLOCK_CHUNK = FILE_HEADER_BYTES + BLOCK_BYTES
const CHUNK_SIZES = [ONE_BLOCK_CHUNK, FILE_HEADER_BYTES + 3 * BLOCK_BYTES, 4 * MIB]

const BLOCKS: BlockSpec[] = []
for (let i = 0; i < 7; i++) BLOCKS.push({ type: 'imu', sensorId: 1 + (i % 2), seq: i, samples: rampSamples(3, i) })
const PARTIAL = 3584
const FILE = encodeLogFile({ sessionId: 9 }, BLOCKS, fill(PARTIAL, 0xff))
const WHOLE = encodeLogFile({ sessionId: 9 }, BLOCKS)

async function source(bytes: Uint8Array): Promise<ByteSource> {
  return new MemDir({ f: bytes }).open('f')
}

/** A source that never returns more than `cap` bytes per read, so the header
 *  and blocks straddle reads (a ByteSource "may return fewer at end of file";
 *  this one always does). */
function capped(bytes: Uint8Array, cap: number): ByteSource {
  return {
    size: bytes.length,
    async read(offset, length) {
      return bytes.slice(offset, offset + Math.min(length, cap))
    },
  }
}

interface Collected {
  header: Uint8Array | null
  blocks: { index: number; bytes: Uint8Array; bytesDone: number }[]
  chunks: number[]
  order: LogFrame['kind'][]
  end: FramingEnd
}

async function collect(src: ByteSource, chunk: number, signal?: AbortSignal): Promise<Collected> {
  const gen = logFrames(src, chunk, signal)
  const out: Collected = { header: null, blocks: [], chunks: [], order: [], end: { blocks: -1, trailingBytes: -1, bytesDone: -1 } }
  for (;;) {
    const next = await gen.next()
    if (next.done) {
      out.end = next.value
      return out
    }
    const f = next.value
    out.order.push(f.kind)
    if (f.kind === 'header') out.header = f.bytes.slice()
    else if (f.kind === 'block') out.blocks.push({ index: f.index, bytes: f.bytes.slice(), bytesDone: f.bytesDone })
    else out.chunks.push(f.bytesDone)
  }
}

function expectFrames(c: Collected, file: Uint8Array, blockCount: number, trailing: number): void {
  expect(c.header).not.toBeNull()
  expect(bytesEqual(c.header!, file.subarray(0, FILE_HEADER_BYTES))).toBe(true)
  expect(c.blocks.map((b) => b.index)).toEqual([...Array(blockCount).keys()])
  for (const b of c.blocks) {
    const off = FILE_HEADER_BYTES + b.index * BLOCK_BYTES
    expect(b.bytes.length).toBe(BLOCK_BYTES)
    expect(bytesEqual(b.bytes, file.subarray(off, off + BLOCK_BYTES))).toBe(true)
    expect(b.bytesDone).toBe(off + BLOCK_BYTES)
  }
  expect(c.end).toEqual({ blocks: blockCount, trailingBytes: trailing, bytesDone: file.length })
  expect(c.chunks[c.chunks.length - 1]).toBe(file.length)
  for (let i = 1; i < c.chunks.length; i++) expect(c.chunks[i]).toBeGreaterThan(c.chunks[i - 1])
  // The header is the first frame that is not a chunk end (short reads
  // may need several reads before the 512 B are complete).
  expect(c.order.find((k) => k !== 'chunk')).toBe('header')
  expect(c.order.filter((k) => k === 'header')).toHaveLength(1)
}

describe('logFrames', () => {
  it.each(CHUNK_SIZES)('yields the header, every whole block and the chunk ends at %d-byte chunks', async (chunk) => {
    expectFrames(await collect(await source(FILE), chunk), FILE, BLOCKS.length, PARTIAL)
    expectFrames(await collect(await source(WHOLE), chunk), WHOLE, BLOCKS.length, 0)
  })

  it('emits one chunk frame per read, after the blocks of that read', async () => {
    const c = await collect(await source(WHOLE), ONE_BLOCK_CHUNK)
    expect(c.order).toEqual(['header', 'block', 'chunk', ...Array(BLOCKS.length - 1).fill(['block', 'chunk']).flat()])
    expect(c.chunks).toEqual(BLOCKS.map((_, i) => ONE_BLOCK_CHUNK + i * BLOCK_BYTES))
  })

  it.each([1, 100, 511, 512, 513, 4095, 4097, 5000])('carries the header and blocks across %d-byte reads', async (cap) => {
    expectFrames(await collect(capped(FILE, cap), 4 * MIB), FILE, BLOCKS.length, PARTIAL)
  })

  it('never yields a trailing partial block, whatever its length', async () => {
    for (const tail of [1, 31, 32, 33, BLOCK_BYTES - 1]) {
      const file = encodeLogFile({}, BLOCKS.slice(0, 2), fill(tail, 0x5a))
      const c = await collect(await source(file), ONE_BLOCK_CHUNK)
      expect(c.blocks).toHaveLength(2)
      expect(c.end).toEqual({ blocks: 2, trailingBytes: tail, bytesDone: file.length })
    }
  })

  it('a file shorter than the header yields no header and reports every byte as trailing', async () => {
    for (const len of [0, 1, 100, FILE_HEADER_BYTES - 1]) {
      const c = await collect(await source(new Uint8Array(len)), 4 * MIB)
      expect(c.header).toBeNull()
      expect(c.blocks).toEqual([])
      expect(c.end).toEqual({ blocks: 0, trailingBytes: len, bytesDone: len })
    }
  })

  it('a header-only file yields the header and nothing else', async () => {
    const c = await collect(await source(encodeLogFile({}, [])), 4 * MIB)
    expect(c.order).toEqual(['header', 'chunk'])
    expect(c.end).toEqual({ blocks: 0, trailingBytes: 0, bytesDone: FILE_HEADER_BYTES })
  })

  it('block views are zero-copy into the chunk (a copy is the caller\'s job)', async () => {
    const src = await source(WHOLE)
    const gen = logFrames(src, 4 * MIB)
    const frames: BlockFrame[] = []
    for await (const f of gen) if (f.kind === 'block') frames.push(f)
    // All blocks of one chunk share that chunk's buffer.
    expect(new Set(frames.map((f) => f.bytes.buffer)).size).toBe(1)
    expect(frames[1].bytes.byteOffset).toBe(FILE_HEADER_BYTES + BLOCK_BYTES)
  })

  it('propagates a read failure and an abort unchanged', async () => {
    const dir = new MemDir({ f: FILE }).failRead('f', ONE_BLOCK_CHUNK)
    await expect(collect(await dir.open('f'), ONE_BLOCK_CHUNK)).rejects.toMatchObject({ name: 'NotReadableError' })
    const ac = new AbortController()
    ac.abort()
    await expect(collect(await source(FILE), ONE_BLOCK_CHUNK, ac.signal)).rejects.toMatchObject({ name: 'AbortError' })
  })
})
