import { describe, expect, it } from 'vitest'
import type { MainToWorker, WorkerToMain } from './convert/protocol'
import type { MetaJson } from './convert/types'
import { ConversionQueue, type ConversionJobSpec, type QueueEvent, type ResolvedJob, type WorkerLike } from './convertQueue'

class FakeWorker implements WorkerLike {
  readonly posted: MainToWorker[] = []
  onmessage: ((ev: MessageEvent<WorkerToMain>) => void) | null = null
  onerror: ((ev: ErrorEvent) => void) | null = null
  terminated = false

  postMessage(msg: MainToWorker): void {
    this.posted.push(msg)
  }

  terminate(): void {
    this.terminated = true
  }

  /** The worker answers (the queue reads only `.data`). */
  emit(msg: WorkerToMain): void {
    this.onmessage?.({ data: msg } as MessageEvent<WorkerToMain>)
  }

  /** The worker script threw (the queue reads only `.message`). */
  crash(message = 'boom'): void {
    this.onerror?.({ message } as ErrorEvent)
  }

  /** The job ids of the 'convert' messages received so far. */
  get converts(): string[] {
    return this.posted.filter((m) => m.type === 'convert').map((m) => m.jobId)
  }
}

/** Handles are opaque to the queue: any object will do. */
const RAW = { kind: 'file', name: 'LOG_0010.BIN' } as unknown as FileSystemFileHandle
const OUT = { kind: 'directory', name: 'sleeve-u7-1' } as unknown as FileSystemDirectoryHandle

const META = { fw: '1.2.0', device_id: 7 } as unknown as MetaJson

function job(name: string, folder = 'sleeve-u7-1'): ConversionJobSpec {
  const stem = name.replace(/\.BIN$/, '')
  return {
    id: `${folder}/${name}`,
    folder,
    localName: name,
    stem,
    sourceFile: name,
    placement: { labels: { 1: 'thigh', 2: 'shin' }, fallback: 'placement not set' },
  }
}

interface Rig {
  queue: ConversionQueue
  events: QueueEvent[]
  workers: FakeWorker[]
  /** Per job id: what resolve() answers (an Error rejects). */
  resolutions: Map<string, Partial<ResolvedJob> | Error>
  /** Events of one kind, ids only. */
  ids(type: QueueEvent['type']): string[]
}

function rig(createWorker?: () => WorkerLike): Rig {
  const events: QueueEvent[] = []
  const workers: FakeWorker[] = []
  const resolutions = new Map<string, Partial<ResolvedJob> | Error>()
  const queue = new ConversionQueue({
    createWorker:
      createWorker ??
      (() => {
        const w = new FakeWorker()
        workers.push(w)
        return w
      }),
    resolve: async (j) => {
      const r = resolutions.get(j.id)
      if (r instanceof Error) throw r
      return { raw: RAW, outDir: OUT, outputsPresent: false, ...r }
    },
    onEvent: (e) => events.push(e),
  })
  return {
    queue,
    events,
    workers,
    resolutions,
    ids: (type) => events.filter((e) => e.type === type).map((e) => e.id),
  }
}

/** Let resolve() and the continuation after it run. */
async function flush(): Promise<void> {
  await new Promise((r) => setTimeout(r, 0))
}

function domError(name: string): DOMException {
  return new DOMException(`injected ${name}`, name)
}

describe('ConversionQueue', () => {
  it('runs jobs one at a time in FIFO order on a single lazily created worker', async () => {
    const r = rig()
    expect(r.queue.enqueue(job('LOG_0010.BIN'))).toBe(true)
    expect(r.queue.enqueue(job('LOG_0011.BIN'))).toBe(true)
    expect(r.ids('queued')).toEqual(['sleeve-u7-1/LOG_0010.BIN', 'sleeve-u7-1/LOG_0011.BIN'])
    expect(r.workers).toHaveLength(0)

    r.queue.start()
    await flush()
    expect(r.workers).toHaveLength(1)
    const w = r.workers[0]
    expect(w.converts).toEqual(['sleeve-u7-1/LOG_0010.BIN'])
    expect(w.posted[0]).toMatchObject({
      type: 'convert',
      raw: RAW,
      outDir: OUT,
      stem: 'LOG_0010',
      sourceFile: 'LOG_0010.BIN',
      placement: { labels: { 1: 'thigh', 2: 'shin' }, fallback: 'placement not set' },
    })
    // start() while a job runs is a no-op
    r.queue.start()
    await flush()
    expect(w.converts).toEqual(['sleeve-u7-1/LOG_0010.BIN'])

    w.emit({ type: 'done', jobId: 'sleeve-u7-1/LOG_0010.BIN', meta: META, summary: 'S', outputs: { csv: 'a', meta: 'b', summary: 'c' } })
    await flush()
    expect(r.workers).toHaveLength(1)
    expect(w.converts).toEqual(['sleeve-u7-1/LOG_0010.BIN', 'sleeve-u7-1/LOG_0011.BIN'])
  })

  it('relays progress and done with the job id and the payload', async () => {
    const r = rig()
    r.queue.enqueue(job('LOG_0010.BIN'))
    r.queue.start()
    await flush()
    const w = r.workers[0]
    w.emit({ type: 'progress', jobId: 'sleeve-u7-1/LOG_0010.BIN', phase: 'prepass', bytesDone: 10, bytesTotal: 100, rows: 0 })
    w.emit({ type: 'progress', jobId: 'sleeve-u7-1/LOG_0010.BIN', phase: 'convert', bytesDone: 50, bytesTotal: 100, rows: 1234 })
    w.emit({ type: 'done', jobId: 'sleeve-u7-1/LOG_0010.BIN', meta: META, summary: 'text', outputs: { csv: 'LOG_0010.csv', meta: 'LOG_0010.meta.json', summary: 'LOG_0010_summary.txt' } })
    expect(r.events.slice(1)).toEqual([
      { type: 'progress', id: 'sleeve-u7-1/LOG_0010.BIN', phase: 'prepass', bytesDone: 10, bytesTotal: 100, rows: 0 },
      { type: 'progress', id: 'sleeve-u7-1/LOG_0010.BIN', phase: 'convert', bytesDone: 50, bytesTotal: 100, rows: 1234 },
      {
        type: 'done',
        id: 'sleeve-u7-1/LOG_0010.BIN',
        meta: META,
        summary: 'text',
        outputs: { csv: 'LOG_0010.csv', meta: 'LOG_0010.meta.json', summary: 'LOG_0010_summary.txt' },
      },
    ])
    expect(r.queue.runningId).toBeNull()
  })

  it('maps a failed worker message and moves on', async () => {
    const r = rig()
    r.queue.enqueue(job('LOG_0010.BIN'))
    r.queue.enqueue(job('LOG_0011.BIN'))
    r.queue.start()
    await flush()
    const w = r.workers[0]
    w.emit({ type: 'failed', jobId: 'sleeve-u7-1/LOG_0010.BIN', code: 'format', detail: 'bad magic' })
    await flush()
    expect(r.events).toContainEqual({ type: 'failed', id: 'sleeve-u7-1/LOG_0010.BIN', code: 'format', detail: 'bad magic' })
    expect(w.converts).toEqual(['sleeve-u7-1/LOG_0010.BIN', 'sleeve-u7-1/LOG_0011.BIN'])
  })

  it('a resolve failure is permission for NotAllowedError / SecurityError and read otherwise', async () => {
    const r = rig()
    r.resolutions.set('sleeve-u7-1/LOG_0010.BIN', domError('NotAllowedError'))
    r.resolutions.set('sleeve-u7-1/LOG_0011.BIN', domError('SecurityError'))
    r.resolutions.set('sleeve-u7-1/LOG_0012.BIN', domError('NotFoundError'))
    for (const n of ['LOG_0010.BIN', 'LOG_0011.BIN', 'LOG_0012.BIN', 'LOG_0013.BIN']) r.queue.enqueue(job(n))
    r.queue.start()
    await flush()
    await flush()
    await flush()
    const failed = r.events.filter((e) => e.type === 'failed')
    expect(failed).toEqual([
      { type: 'failed', id: 'sleeve-u7-1/LOG_0010.BIN', code: 'permission', detail: 'NotAllowedError: injected NotAllowedError' },
      { type: 'failed', id: 'sleeve-u7-1/LOG_0011.BIN', code: 'permission', detail: 'SecurityError: injected SecurityError' },
      { type: 'failed', id: 'sleeve-u7-1/LOG_0012.BIN', code: 'read', detail: 'NotFoundError: injected NotFoundError' },
    ])
    // the worker was only ever needed for the fourth job
    expect(r.workers).toHaveLength(1)
    expect(r.workers[0].converts).toEqual(['sleeve-u7-1/LOG_0013.BIN'])
  })

  it('short-circuits a job whose three outputs already exist (decision V)', async () => {
    const r = rig()
    r.resolutions.set('sleeve-u7-1/LOG_0010.BIN', { outputsPresent: true })
    r.queue.enqueue(job('LOG_0010.BIN'))
    r.queue.enqueue(job('LOG_0011.BIN'))
    r.queue.start()
    await flush()
    await flush()
    expect(r.events).toContainEqual({
      type: 'already-converted',
      id: 'sleeve-u7-1/LOG_0010.BIN',
      outputs: { csv: 'LOG_0010.csv', meta: 'LOG_0010.meta.json', summary: 'LOG_0010_summary.txt' },
    })
    expect(r.workers[0].converts).toEqual(['sleeve-u7-1/LOG_0011.BIN'])
  })

  it('ignores a duplicate enqueue while the job is waiting or running', async () => {
    const r = rig()
    expect(r.queue.enqueue(job('LOG_0010.BIN'))).toBe(true)
    expect(r.queue.enqueue(job('LOG_0010.BIN'))).toBe(false)
    r.queue.start()
    await flush()
    expect(r.queue.runningId).toBe('sleeve-u7-1/LOG_0010.BIN')
    expect(r.queue.enqueue(job('LOG_0010.BIN'))).toBe(false)
    expect(r.ids('queued')).toEqual(['sleeve-u7-1/LOG_0010.BIN'])
  })

  it('cancels a waiting job by dropping it and a running job by asking the worker', async () => {
    const r = rig()
    r.queue.enqueue(job('LOG_0010.BIN'))
    r.queue.enqueue(job('LOG_0011.BIN'))
    r.queue.start()
    await flush()
    r.queue.cancel('sleeve-u7-1/LOG_0011.BIN')
    expect(r.queue.queuedIds).toEqual([])
    expect(r.events.at(-1)).toEqual({ type: 'cancelled', id: 'sleeve-u7-1/LOG_0011.BIN' })

    r.queue.cancel('sleeve-u7-1/LOG_0010.BIN')
    const w = r.workers[0]
    expect(w.posted.at(-1)).toEqual({ type: 'cancel', jobId: 'sleeve-u7-1/LOG_0010.BIN' })
    // the running job is only over when the worker says so
    expect(r.queue.runningId).toBe('sleeve-u7-1/LOG_0010.BIN')
    w.emit({ type: 'cancelled', jobId: 'sleeve-u7-1/LOG_0010.BIN' })
    expect(r.events.at(-1)).toEqual({ type: 'cancelled', id: 'sleeve-u7-1/LOG_0010.BIN' })
    expect(r.queue.runningId).toBeNull()
    // an unknown id is ignored
    r.queue.cancel('nope')
    expect(r.events).toHaveLength(4)
  })

  it('a cancel that lands while the handles are still resolving never reaches the worker', async () => {
    let release: (v: ResolvedJob) => void = () => undefined
    const events: QueueEvent[] = []
    const w = new FakeWorker()
    const queue = new ConversionQueue({
      createWorker: () => w,
      resolve: () => new Promise<ResolvedJob>((res) => (release = res)),
      onEvent: (e) => events.push(e),
    })
    queue.enqueue(job('LOG_0010.BIN'))
    queue.start()
    queue.cancel('sleeve-u7-1/LOG_0010.BIN')
    release({ raw: RAW, outDir: OUT, outputsPresent: false })
    await flush()
    expect(events.at(-1)).toEqual({ type: 'cancelled', id: 'sleeve-u7-1/LOG_0010.BIN' })
    expect(w.posted).toEqual([])
    expect(queue.runningId).toBeNull()
  })

  it('accepts a job again after it failed (retry) and after it was cancelled', async () => {
    const r = rig()
    r.queue.enqueue(job('LOG_0010.BIN'))
    r.queue.start()
    await flush()
    r.workers[0].emit({ type: 'failed', jobId: 'sleeve-u7-1/LOG_0010.BIN', code: 'write' })
    expect(r.queue.enqueue(job('LOG_0010.BIN'))).toBe(true)
    r.queue.start()
    await flush()
    expect(r.workers[0].converts).toEqual(['sleeve-u7-1/LOG_0010.BIN', 'sleeve-u7-1/LOG_0010.BIN'])
    r.queue.cancel('sleeve-u7-1/LOG_0010.BIN')
    r.workers[0].emit({ type: 'cancelled', jobId: 'sleeve-u7-1/LOG_0010.BIN' })
    expect(r.queue.enqueue(job('LOG_0010.BIN'))).toBe(true)
  })

  it('a worker error fails the running job with worker and the next job gets a fresh worker', async () => {
    const r = rig()
    r.queue.enqueue(job('LOG_0010.BIN'))
    r.queue.enqueue(job('LOG_0011.BIN'))
    r.queue.start()
    await flush()
    const first = r.workers[0]
    first.crash('script error')
    await flush()
    expect(r.events).toContainEqual({ type: 'failed', id: 'sleeve-u7-1/LOG_0010.BIN', code: 'worker', detail: 'script error' })
    expect(first.terminated).toBe(true)
    expect(r.workers).toHaveLength(2)
    expect(r.workers[1].converts).toEqual(['sleeve-u7-1/LOG_0011.BIN'])
    // a late message from the dead worker is ignored
    first.emit({ type: 'done', jobId: 'sleeve-u7-1/LOG_0010.BIN', meta: META, summary: '', outputs: { csv: '', meta: '', summary: '' } })
    expect(r.ids('done')).toEqual([])
  })

  it('a message about a job the queue is not running fails the running job with worker', async () => {
    const r = rig()
    r.queue.enqueue(job('LOG_0010.BIN'))
    r.queue.start()
    await flush()
    const w = r.workers[0]
    w.emit({ type: 'done', jobId: 'sleeve-u7-1/LOG_9999.BIN', meta: META, summary: '', outputs: { csv: '', meta: '', summary: '' } })
    expect(r.events.at(-1)).toMatchObject({ type: 'failed', id: 'sleeve-u7-1/LOG_0010.BIN', code: 'worker' })
    expect(w.terminated).toBe(true)
    // nothing running: a stray message is dropped
    w.emit({ type: 'progress', jobId: 'x', phase: 'convert', bytesDone: 1, bytesTotal: 1, rows: 1 })
    expect(r.ids('progress')).toEqual([])
  })

  it('reports worker when the worker cannot be created at all', async () => {
    const r = rig(() => {
      throw new Error('Worker is not defined')
    })
    r.queue.enqueue(job('LOG_0010.BIN'))
    r.queue.start()
    await flush()
    expect(r.events.at(-1)).toEqual({
      type: 'failed',
      id: 'sleeve-u7-1/LOG_0010.BIN',
      code: 'worker',
      detail: 'Error: Worker is not defined',
    })
  })

  it('dispose cancels the waiting and running jobs, terminates the worker and refuses new work', async () => {
    const r = rig()
    r.queue.enqueue(job('LOG_0010.BIN'))
    r.queue.enqueue(job('LOG_0011.BIN'))
    r.queue.start()
    await flush()
    r.queue.dispose()
    expect(r.ids('cancelled')).toEqual(['sleeve-u7-1/LOG_0011.BIN', 'sleeve-u7-1/LOG_0010.BIN'])
    expect(r.workers[0].terminated).toBe(true)
    expect(r.queue.runningId).toBeNull()
    expect(r.queue.enqueue(job('LOG_0012.BIN'))).toBe(false)
    r.queue.start()
    await flush()
    expect(r.workers).toHaveLength(1)
    // idempotent
    r.queue.dispose()
    expect(r.events).toHaveLength(4)
  })
})
