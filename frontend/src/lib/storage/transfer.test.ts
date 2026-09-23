import { describe, expect, it } from 'vitest'
import { STORAGE_EXPECTED_BYTES_PER_S, STORAGE_RATE_EWMA_ALPHA } from '../config'
import { BLOCK_BYTES, FILE_HEADER_BYTES } from './binFormat'
import { crc32Of } from './crc32'
import { encodeLogFile, rampSamples, withByteFlipped } from './fixtures/binEncode'
import { fixtureBytes, LOG_0010_HEAD_TXT } from './fixtures/load'
import { bytesEqual } from './io'
import { MemDir } from './memDir'
import { runTransfer, type TransferEvent, type TransferItem, type TransferSummary } from './transfer'

const BIN = encodeLogFile({ deviceId: 1, sourceId: 0, sessionId: 1 }, [
  { type: 'imu', sensorId: 1, seq: 0, samples: rampSamples(10) },
  { type: 'imu', sensorId: 2, seq: 1, samples: rampSamples(10) },
  { type: 'sync', seq: 2, espUs: 5n, unixUs: 6n },
  { type: 'imu', sensorId: 1, seq: 3, samples: rampSamples(4) },
  { type: 'end', seq: 4 },
])
const TXT = fixtureBytes(LOG_0010_HEAD_TXT)
const FOLDER = 'sleeve-u1-0'
/** Small enough that the BIN takes several reads (header + 1 block each). */
const CHUNK = FILE_HEADER_BYTES + BLOCK_BYTES

function items(card: MemDir): TransferItem[] {
  return [...card.files.entries()].map(([name, bytes]) => ({
    id: name,
    name,
    size: bytes.length,
    kind: name.endsWith('.BIN') ? 'BIN' : 'TXT',
    folder: FOLDER,
  }))
}

interface Run {
  events: TransferEvent[]
  summary: TransferSummary
}

async function run(
  card: MemDir,
  dest: MemDir,
  opts: { keepCopies?: boolean; signal?: AbortSignal; onEvent?: (e: TransferEvent) => void; now?: () => number } = {},
  list = items(card),
): Promise<Run> {
  const events: TransferEvent[] = []
  const gen = runTransfer(list, { card, dest, keepCopies: opts.keepCopies ?? false, now: opts.now }, { signal: opts.signal, chunkBytes: CHUNK })
  for (;;) {
    const next = await gen.next()
    if (next.done) return { events, summary: next.value }
    events.push(next.value)
    opts.onEvent?.(next.value)
  }
}

const ofType = <T extends TransferEvent['type']>(events: TransferEvent[], type: T) =>
  events.filter((e): e is Extract<TransferEvent, { type: T }> => e.type === type)

function freshCard(): MemDir {
  return new MemDir({ 'LOG_0001.BIN': BIN, 'LOG_0001.TXT': TXT })
}

/** Create <folder>/raw ahead of the engine so a test can pre-populate it. */
async function rawOf(dest: MemDir): Promise<MemDir> {
  await (await dest.subdir(FOLDER, true)).subdir('raw', true)
  return raw(dest)
}

function raw(dest: MemDir): MemDir {
  const dir = dest.at(FOLDER, 'raw')
  if (!dir) throw new Error('raw dir missing')
  return dir
}

describe('runTransfer happy path', () => {
  it('copies to <folder>/raw, verifies, reports scans and deletes from the card', async () => {
    const card = freshCard()
    const dest = new MemDir()
    const { events, summary } = await run(card, dest)

    expect(ofType(events, 'item-start').map((e) => e.id)).toEqual(['LOG_0001.BIN', 'LOG_0001.TXT'])
    const done = ofType(events, 'item-done')
    expect(done).toHaveLength(2)
    expect(done[0]).toMatchObject({ id: 'LOG_0001.BIN', result: 'copied', deleted: true, localName: 'LOG_0001.BIN' })
    expect(done[0].scan).toMatchObject({ blocks: 5, valid: 5, cleanEnd: true, samples: { 1: 14, 2: 10 } })
    expect(done[0].scan?.syncBlocks).toEqual([{ espUs: 5n, unixUs: 6n, source: 0 }])
    expect(done[1]).toMatchObject({ id: 'LOG_0001.TXT', result: 'copied', deleted: true, localName: 'LOG_0001.TXT' })
    expect(done[1].scan).toBeUndefined()
    expect(ofType(events, 'item-failed')).toEqual([])

    expect(bytesEqual(raw(dest).bytes('LOG_0001.BIN')!, BIN)).toBe(true)
    expect(bytesEqual(raw(dest).bytes('LOG_0001.TXT')!, TXT)).toBe(true)
    expect(card.files.size).toBe(0)

    expect(events[events.length - 1]).toEqual({ type: 'done', summary })
    expect(summary).toMatchObject({
      items: 2,
      copied: 2,
      alreadyTransferred: 0,
      failed: 0,
      deleted: 2,
      notStarted: 0,
      aborted: false,
      bytesCopied: BIN.length + TXT.length,
    })

    // The copy phase reported the BIN chunk by chunk, then verify re-read it.
    const copyProgress = ofType(events, 'progress').filter((e) => e.id === 'LOG_0001.BIN' && e.phase === 'copy')
    expect(copyProgress.map((e) => e.bytesDone)).toEqual([CHUNK, CHUNK + BLOCK_BYTES, CHUNK + 2 * BLOCK_BYTES, CHUNK + 3 * BLOCK_BYTES, BIN.length])
    expect(copyProgress.every((e) => e.bytesTotal === BIN.length)).toBe(true)
    expect(ofType(events, 'progress').some((e) => e.id === 'LOG_0001.BIN' && e.phase === 'verify')).toBe(true)
  })

  it('keeps the card untouched with keepCopies', async () => {
    const card = freshCard()
    const dest = new MemDir()
    const { events, summary } = await run(card, dest, { keepCopies: true })
    expect(ofType(events, 'item-done').map((e) => e.deleted)).toEqual([false, false])
    expect(card.files.size).toBe(2)
    expect(summary).toMatchObject({ copied: 2, deleted: 0 })
    expect(bytesEqual(raw(dest).bytes('LOG_0001.BIN')!, BIN)).toBe(true)
  })

  it('feeds the rate EWMA from the copy phase and estimates the rest of the run', async () => {
    const card = new MemDir({ 'LOG_0001.BIN': BIN })
    const dest = new MemDir()
    const STEP = 100
    let t = 0
    const { events } = await run(card, dest, { keepCopies: true, now: () => (t += STEP) })
    const first = ofType(events, 'progress')[0]
    expect(first.phase).toBe('copy')
    const instant = (CHUNK * 1000) / STEP
    expect(first.bytesPerS).toBeCloseTo(STORAGE_RATE_EWMA_ALPHA * instant + (1 - STORAGE_RATE_EWMA_ALPHA) * STORAGE_EXPECTED_BYTES_PER_S, 6)
    expect(first.runBytesTotal).toBe(BIN.length)
    expect(first.runBytesDone).toBe(CHUNK)
    expect(first.etaMs).toBe(Math.ceil(((BIN.length - CHUNK) / first.bytesPerS) * 1000))
  })
})

describe('runTransfer duplicates', () => {
  it('reports already-transferred when an identical copy exists and removes the fresh duplicate', async () => {
    const card = freshCard()
    const dest = new MemDir()
    ;(await rawOf(dest)).put('LOG_0001.BIN', BIN)
    const { events, summary } = await run(card, dest)
    const bin = ofType(events, 'item-done').find((e) => e.id === 'LOG_0001.BIN')
    expect(bin).toMatchObject({ result: 'already-transferred', deleted: true, localName: 'LOG_0001.BIN' })
    expect([...raw(dest).files.keys()].sort()).toEqual(['LOG_0001.BIN', 'LOG_0001.TXT'])
    expect(card.files.has('LOG_0001.BIN')).toBe(false)
    expect(summary).toMatchObject({ copied: 1, alreadyTransferred: 1, deleted: 2 })
    expect(ofType(events, 'progress').some((e) => e.phase === 'probe')).toBe(true)
  })

  it('writes LOG_0001-2.BIN when a different file of that name and size exists', async () => {
    const card = freshCard()
    const dest = new MemDir()
    const other = withByteFlipped(BIN, FILE_HEADER_BYTES + 40)
    ;(await rawOf(dest)).put('LOG_0001.BIN', other)
    const { events } = await run(card, dest)
    const bin = ofType(events, 'item-done').find((e) => e.id === 'LOG_0001.BIN')
    expect(bin).toMatchObject({ result: 'copied', deleted: true, localName: 'LOG_0001-2.BIN' })
    expect(bytesEqual(raw(dest).bytes('LOG_0001.BIN')!, other)).toBe(true)
    expect(bytesEqual(raw(dest).bytes('LOG_0001-2.BIN')!, BIN)).toBe(true)
  })

  it('skips the probe CRC and still suffixes when the existing file has another size', async () => {
    const card = freshCard()
    const dest = new MemDir()
    ;(await rawOf(dest)).put('LOG_0001.BIN', BIN.subarray(0, 600)).put('LOG_0001-2.BIN', 'taken')
    const { events } = await run(card, dest, { keepCopies: true })
    const bin = ofType(events, 'item-done').find((e) => e.id === 'LOG_0001.BIN')
    expect(bin).toMatchObject({ result: 'copied', localName: 'LOG_0001-3.BIN' })
    expect(ofType(events, 'progress').some((e) => e.phase === 'probe')).toBe(false)
  })
})

describe('runTransfer failures never touch the card', () => {
  it('verify mismatch: removes the bad local copy, keeps the card file', async () => {
    const card = freshCard()
    const dest = new MemDir().corruptOnClose('LOG_0001.BIN').corruptOnClose('LOG_0001.TXT')
    const { events, summary } = await run(card, dest)
    const failed = ofType(events, 'item-failed')
    expect(failed).toHaveLength(2)
    expect(failed[0]).toMatchObject({ id: 'LOG_0001.BIN', code: 'verify-mismatch', phase: 'verify' })
    expect(failed[1]).toMatchObject({ id: 'LOG_0001.TXT', code: 'verify-mismatch', phase: 'verify' })
    expect(raw(dest).files.size).toBe(0)
    expect(card.files.size).toBe(2)
    expect(summary).toMatchObject({ copied: 0, failed: 2, deleted: 0 })
  })

  it('card read failure mid-copy: card-read, nothing written, nothing deleted', async () => {
    const card = freshCard().failRead('LOG_0001.BIN', CHUNK + BLOCK_BYTES)
    const dest = new MemDir()
    const { events, summary } = await run(card, dest)
    const failed = ofType(events, 'item-failed')
    expect(failed[0]).toMatchObject({ id: 'LOG_0001.BIN', code: 'card-read', phase: 'copy' })
    expect(failed[0].detail).toContain('NotReadableError')
    expect(raw(dest).files.has('LOG_0001.BIN')).toBe(false)
    expect(card.files.has('LOG_0001.BIN')).toBe(true)
    // The next item still ran.
    expect(ofType(events, 'item-done').map((e) => e.id)).toEqual(['LOG_0001.TXT'])
    expect(summary).toMatchObject({ copied: 1, failed: 1, deleted: 1, aborted: false })
  })

  it('unplugged card: every item fails with card-read', async () => {
    const card = freshCard().unplug()
    const dest = new MemDir()
    const { events } = await run(card, dest)
    expect(ofType(events, 'item-failed').map((e) => e.code)).toEqual(['card-read', 'card-read'])
    expect(raw(dest).files.size).toBe(0)
  })

  it('maps NotAllowedError on the card to permission', async () => {
    const card = freshCard().failOpen('LOG_0001.BIN', 'NotAllowedError')
    const { events } = await run(card, new MemDir())
    expect(ofType(events, 'item-failed')[0]).toMatchObject({ id: 'LOG_0001.BIN', code: 'permission', phase: 'copy' })
    expect(card.files.size).toBe(2 - 1)
  })

  it('maps destination write and close failures to dest-write', async () => {
    const card = freshCard()
    const dest = new MemDir().failWrite('LOG_0001.BIN').failClose('LOG_0001.TXT')
    const { events } = await run(card, dest)
    const failed = ofType(events, 'item-failed')
    expect(failed[0]).toMatchObject({ id: 'LOG_0001.BIN', code: 'dest-write', phase: 'copy' })
    expect(failed[0].detail).toContain('QuotaExceededError')
    expect(failed[1]).toMatchObject({ id: 'LOG_0001.TXT', code: 'dest-write', phase: 'copy' })
    expect(failed[1].detail).toContain('AbortError')
    expect(raw(dest).files.size).toBe(0)
    expect(card.files.size).toBe(2)
  })

  it('delete failure: card-delete with the verified copy kept', async () => {
    const card = freshCard().failRemove('LOG_0001.BIN')
    const dest = new MemDir()
    const { events, summary } = await run(card, dest)
    const failed = ofType(events, 'item-failed')
    expect(failed).toHaveLength(1)
    expect(failed[0]).toMatchObject({ id: 'LOG_0001.BIN', code: 'card-delete', phase: 'delete', localName: 'LOG_0001.BIN' })
    expect(failed[0].scan?.valid).toBe(5)
    expect(bytesEqual(raw(dest).bytes('LOG_0001.BIN')!, BIN)).toBe(true)
    expect(card.files.has('LOG_0001.BIN')).toBe(true)
    expect(card.files.has('LOG_0001.TXT')).toBe(false)
    expect(summary).toMatchObject({ copied: 1, failed: 1, deleted: 1 })
  })
})

describe('runTransfer abort', () => {
  it('stops after the current item reports aborted; nothing deleted, no partial copy', async () => {
    const card = freshCard()
    const dest = new MemDir()
    const ac = new AbortController()
    const { events, summary } = await run(card, dest, {
      signal: ac.signal,
      onEvent: (e) => {
        if (e.type === 'progress' && e.phase === 'copy' && e.bytesDone === CHUNK) ac.abort()
      },
    })
    const failed = ofType(events, 'item-failed')
    expect(failed).toHaveLength(1)
    expect(failed[0]).toMatchObject({ id: 'LOG_0001.BIN', code: 'aborted', phase: 'copy' })
    expect(ofType(events, 'item-start')).toHaveLength(1)
    expect(dest.at(FOLDER, 'raw')?.files.size ?? 0).toBe(0)
    expect(card.files.size).toBe(2)
    expect(summary).toMatchObject({ aborted: true, notStarted: 1, failed: 1, copied: 0, deleted: 0 })
  })

  it('an already-aborted signal starts nothing', async () => {
    const ac = new AbortController()
    ac.abort()
    const card = freshCard()
    const { events, summary } = await run(card, new MemDir(), { signal: ac.signal })
    expect(events).toEqual([{ type: 'done', summary }])
    expect(summary).toMatchObject({ aborted: true, notStarted: 2, items: 2 })
    expect(card.files.size).toBe(2)
  })

  it('aborting during verify drops the unverified copy and skips the delete', async () => {
    const card = freshCard()
    const dest = new MemDir()
    const ac = new AbortController()
    const { events } = await run(card, dest, {
      signal: ac.signal,
      onEvent: (e) => {
        if (e.type === 'progress' && e.phase === 'verify') ac.abort()
      },
    })
    expect(ofType(events, 'item-failed')[0]).toMatchObject({ id: 'LOG_0001.BIN', code: 'aborted', phase: 'verify' })
    expect(raw(dest).files.size).toBe(0)
    expect(card.files.size).toBe(2)
  })
})

describe('MemDir sinks', () => {
  it('commit on close only and discard on abort', async () => {
    const dir = new MemDir()
    const sink = await dir.create('a')
    await sink.write(new Uint8Array([1, 2]))
    expect(await dir.exists('a')).toBe(false)
    await sink.close()
    expect([...dir.bytes('a')!]).toEqual([1, 2])
    const gone = await dir.create('b')
    await gone.write(new Uint8Array([3]))
    await gone.abort()
    expect(await dir.exists('b')).toBe(false)
    expect(crc32Of(dir.bytes('a')!)).toBe(crc32Of(new Uint8Array([1, 2])))
  })
})

describe('bad blocks are confirmed against the card before the delete', () => {
  const BAD_BIN = encodeLogFile({ deviceId: 1, sourceId: 0, sessionId: 2 }, [
    { type: 'imu', sensorId: 1, seq: 0, samples: rampSamples(10) },
    { type: 'imu', sensorId: 2, seq: 1, samples: rampSamples(10), corruptCrc: true },
    { type: 'end', seq: 2 },
  ])

  it('deletes when the bad block re-reads identically', async () => {
    const card = new MemDir({ 'LOG_0002.BIN': BAD_BIN })
    const dest = new MemDir()
    const { events, summary } = await run(card, dest)
    const done = ofType(events, 'item-done')
    expect(done).toHaveLength(1)
    expect(done[0].scan?.badBlocks).toEqual([1])
    expect(done[0].deleted).toBe(true)
    expect(card.files.has('LOG_0002.BIN')).toBe(false)
    expect(summary.failed).toBe(0)
  })

  it('keeps the card file and drops the copy when the card re-reads differently', async () => {
    const card = new MemDir({ 'LOG_0002.BIN': BAD_BIN })
    const dest = new MemDir()
    let poisoned = false
    const { events, summary } = await run(card, dest, {
      onEvent: (e) => {
        // The copy is complete once verify starts; a byte inside bad block 1
        // then changes on the card, as a transient read error would look.
        if (!poisoned && e.type === 'progress' && e.phase === 'verify') {
          poisoned = true
          const live = card.files.get('LOG_0002.BIN')
          if (!live) throw new Error('card file vanished')
          live[FILE_HEADER_BYTES + BLOCK_BYTES + 100] ^= 0xff
        }
      },
    })
    expect(poisoned).toBe(true)
    const failed = ofType(events, 'item-failed')
    expect(failed).toHaveLength(1)
    expect(failed[0].code).toBe('verify-mismatch')
    expect(failed[0].detail).toContain('bad block 1')
    expect(card.files.has('LOG_0002.BIN')).toBe(true)
    expect(raw(dest).files.has('LOG_0002.BIN')).toBe(false)
    expect(summary.failed).toBe(1)
    expect(summary.deleted).toBe(0)
  })
})

describe('an abandoned run leaves nothing dangling', () => {
  it('generator.return() mid-copy aborts the open writable and drops the partial copy', async () => {
    const card = freshCard()
    const dest = new MemDir()
    const gen = runTransfer(items(card), { card, dest, keepCopies: false }, { chunkBytes: CHUNK })
    for (;;) {
      const next = await gen.next()
      if (next.done) throw new Error('run finished before a copy progress event')
      if (next.value.type === 'progress' && next.value.phase === 'copy') break
    }
    expect(raw(dest).openSinks).toBe(1)
    await gen.return(undefined as unknown as TransferSummary)
    expect(raw(dest).openSinks).toBe(0)
    expect(raw(dest).files.size).toBe(0)
    expect(card.files.has('LOG_0001.BIN')).toBe(true)
  })

  it('generator.return() during verify removes the unverified copy and keeps the card file', async () => {
    const card = freshCard()
    const dest = new MemDir()
    const gen = runTransfer(items(card), { card, dest, keepCopies: false }, { chunkBytes: CHUNK })
    for (;;) {
      const next = await gen.next()
      if (next.done) throw new Error('run finished before a verify progress event')
      if (next.value.type === 'progress' && next.value.phase === 'verify') break
    }
    expect(raw(dest).files.has('LOG_0001.BIN')).toBe(true)
    await gen.return(undefined as unknown as TransferSummary)
    expect(raw(dest).files.has('LOG_0001.BIN')).toBe(false)
    expect(raw(dest).openSinks).toBe(0)
    expect(card.files.has('LOG_0001.BIN')).toBe(true)
  })
})
