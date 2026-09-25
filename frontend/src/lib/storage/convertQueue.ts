// The page side of the conversion (agent-docs/03_PLAN_csv_summary 4.5,
// decisions L and Q): one Web Worker, a FIFO of jobs, events back to the
// page. The transfer loop enqueues and calls start() without awaiting
// anything, so a conversion runs while the next file is still copying from
// the card. Pure over two injected seams (createWorker, resolve) so the
// tests drive it with a fake worker and no File System Access API.
//
// Per job: resolve the handles (main thread, so a withdrawn folder grant is
// seen here as 'permission'); if all three outputs exist already, report
// 'already-converted' without touching the worker (decision V); else post
// 'convert' and relay progress / done / failed / cancelled. A worker error
// (or a message about a job this queue does not know) fails the running job
// with 'worker' and the next job gets a fresh worker.
import { isPermissionError, type ConversionErrorCode, type MainToWorker, type Placement, type WorkerToMain } from './convert/protocol'
import { outputNames, type ConvertPhase, type MetaJson, type OutputNames } from './convert/types'
import { errorDetail } from './io'

export interface ConversionJobSpec {
  /** `<folder>/<localName>`: one job per raw file, retried under the same id. */
  id: string
  folder: string
  localName: string
  stem: string
  /** meta.source_file: the raw file's name on the PC. */
  sourceFile: string
  placement: Placement
}

export type QueueEvent =
  | { type: 'queued'; id: string }
  | { type: 'progress'; id: string; phase: ConvertPhase; bytesDone: number; bytesTotal: number; rows: number }
  | { type: 'done'; id: string; meta: MetaJson; summary: string; outputs: OutputNames }
  | { type: 'already-converted'; id: string; outputs: OutputNames }
  | { type: 'failed'; id: string; code: ConversionErrorCode; detail?: string }
  | { type: 'cancelled'; id: string }

/** The slice of the Worker interface the queue uses; tests pass a fake. The
 *  handler parameters are the DOM event types so a real Worker is assignable
 *  under strictFunctionTypes (only `.data` and `.message` are read). */
export interface WorkerLike {
  postMessage(msg: MainToWorker): void
  onmessage: ((ev: MessageEvent<WorkerToMain>) => void) | null
  onerror: ((ev: ErrorEvent) => void) | null
  terminate(): void
}

export interface ResolvedJob {
  raw: FileSystemFileHandle
  outDir: FileSystemDirectoryHandle
  /** All three outputs already sit beside raw/ (decision V). */
  outputsPresent: boolean
}

export interface QueueDeps {
  createWorker: () => WorkerLike
  /** The page re-derives the handles from the destination root it holds. */
  resolve: (job: ConversionJobSpec) => Promise<ResolvedJob>
  onEvent: (event: QueueEvent) => void
}

export class ConversionQueue {
  private readonly waiting: ConversionJobSpec[] = []
  private running: ConversionJobSpec | null = null
  /** The running job has been posted to the worker (else resolve() is in flight). */
  private posted = false
  /** cancel() arrived while resolve() was in flight. */
  private cancelPending = false
  private worker: WorkerLike | null = null
  private disposed = false

  constructor(private readonly deps: QueueDeps) {}

  /** Ids waiting, in order (tests and diagnostics). */
  get queuedIds(): string[] {
    return this.waiting.map((j) => j.id)
  }

  get runningId(): string | null {
    return this.running?.id ?? null
  }

  /** Append a job. False (and nothing emitted) when a job with that id is
   *  already waiting or running, or the queue is disposed; a job that has
   *  finished, failed or been cancelled is accepted again (a retry). */
  enqueue(job: ConversionJobSpec): boolean {
    if (this.disposed) return false
    if (this.running?.id === job.id || this.waiting.some((j) => j.id === job.id)) return false
    this.waiting.push(job)
    this.deps.onEvent({ type: 'queued', id: job.id })
    return true
  }

  /** Run the head of the queue unless a job is already running. Returns at
   *  once; the outcome arrives through onEvent. */
  start(): void {
    if (this.disposed || this.running) return
    const job = this.waiting.shift()
    if (!job) return
    this.running = job
    this.posted = false
    this.cancelPending = false
    void this.run(job)
  }

  /** Drop a waiting job, or ask the worker to abort the running one. */
  cancel(id: string): void {
    const at = this.waiting.findIndex((j) => j.id === id)
    if (at >= 0) {
      this.waiting.splice(at, 1)
      this.deps.onEvent({ type: 'cancelled', id })
      return
    }
    if (this.running?.id !== id) return
    if (this.posted) this.worker?.postMessage({ type: 'cancel', jobId: id })
    else this.cancelPending = true
  }

  /** Cancel everything and terminate the worker (page unmount). */
  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    for (const job of this.waiting.splice(0)) this.deps.onEvent({ type: 'cancelled', id: job.id })
    const running = this.running
    this.running = null
    this.dropWorker()
    if (running) this.deps.onEvent({ type: 'cancelled', id: running.id })
  }

  // ----- internals --------------------------------------------------------

  private async run(job: ConversionJobSpec): Promise<void> {
    let resolved: ResolvedJob
    try {
      resolved = await this.deps.resolve(job)
    } catch (err) {
      if (this.running !== job) return
      const code: ConversionErrorCode = isPermissionError(err) ? 'permission' : 'read'
      this.finish(job, { type: 'failed', id: job.id, code, detail: errorDetail(err) })
      return
    }
    if (this.running !== job) return
    if (this.cancelPending) {
      this.finish(job, { type: 'cancelled', id: job.id })
      return
    }
    if (resolved.outputsPresent) {
      this.finish(job, { type: 'already-converted', id: job.id, outputs: outputNames(job.stem) })
      return
    }
    let worker: WorkerLike
    try {
      worker = this.ensureWorker()
    } catch (err) {
      this.finish(job, { type: 'failed', id: job.id, code: 'worker', detail: errorDetail(err) })
      return
    }
    this.posted = true
    worker.postMessage({
      type: 'convert',
      jobId: job.id,
      raw: resolved.raw,
      outDir: resolved.outDir,
      stem: job.stem,
      sourceFile: job.sourceFile,
      placement: job.placement,
    })
  }

  private ensureWorker(): WorkerLike {
    if (this.worker) return this.worker
    const worker = this.deps.createWorker()
    worker.onmessage = (ev) => this.onMessage(ev.data)
    worker.onerror = (ev) => this.onWorkerError(ev)
    this.worker = worker
    return worker
  }

  private dropWorker(): void {
    const w = this.worker
    this.worker = null
    if (!w) return
    w.onmessage = null
    w.onerror = null
    w.terminate()
  }

  private onMessage(msg: WorkerToMain): void {
    const job = this.running
    if (!job || !this.posted) return
    if (msg.jobId !== job.id) {
      // the worker and this queue disagree about what is running: neither
      // side can trust the other any more
      this.dropWorker()
      this.finish(job, { type: 'failed', id: job.id, code: 'worker', detail: `unexpected message for ${msg.jobId}` })
      return
    }
    switch (msg.type) {
      case 'progress':
        this.deps.onEvent({
          type: 'progress',
          id: job.id,
          phase: msg.phase,
          bytesDone: msg.bytesDone,
          bytesTotal: msg.bytesTotal,
          rows: msg.rows,
        })
        return
      case 'done':
        this.finish(job, { type: 'done', id: job.id, meta: msg.meta, summary: msg.summary, outputs: msg.outputs })
        return
      case 'failed':
        this.finish(job, { type: 'failed', id: job.id, code: msg.code, detail: msg.detail })
        return
      case 'cancelled':
        this.finish(job, { type: 'cancelled', id: job.id })
        return
    }
  }

  private onWorkerError(ev: ErrorEvent): void {
    this.dropWorker()
    const job = this.running
    if (!job) return
    this.finish(job, { type: 'failed', id: job.id, code: 'worker', detail: ev.message || undefined })
  }

  /** Report the running job's outcome and move on to the next one. */
  private finish(job: ConversionJobSpec, event: QueueEvent): void {
    if (this.running === job) {
      this.running = null
      this.posted = false
      this.cancelPending = false
    }
    this.deps.onEvent(event)
    this.start()
  }
}
