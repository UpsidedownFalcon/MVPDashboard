// The conversion worker (agent-docs/03_PLAN_csv_summary 4.5): a thin shell
// that turns the handles it is posted into the pipeline's ByteSource and
// DirLike and relays progress. Everything that decides anything lives in
// lib/storage/convert/*; this file only maps messages to calls and errors
// to codes. Jobs arrive one at a time (the main-thread ConversionQueue posts
// the next only after this one answered), so there is no queue here, just a
// controller per job so 'cancel' aborts the right one. Nothing at module
// top level touches window or document: fsa.ts only reads window inside
// functions this worker never calls.
//
// Single tsconfig with the DOM lib: `self` is typed through a local
// interface rather than the WebWorker lib.
import {
  STORAGE_CSV_WRITE_CHUNK_BYTES,
  STORAGE_GAP_US,
  STORAGE_NOISE_F_CUT_HZ,
  STORAGE_READ_CHUNK_BYTES,
  STORAGE_TS_OUTLIER_US,
} from '../lib/config'
import { convertAndSummarize } from '../lib/storage/convert/pipeline'
import { isPermissionError, type ConversionErrorCode, type MainToWorker, type WorkerToMain } from '../lib/storage/convert/protocol'
import { ConvertError, type ConvertPhase, type ConvertProgress } from '../lib/storage/convert/types'
import { FileSource, fsaDir } from '../lib/storage/fsa'
import { errorDetail, errorName } from '../lib/storage/io'

interface WorkerScope {
  onmessage: ((ev: MessageEvent<MainToWorker>) => void) | null
  postMessage(msg: WorkerToMain): void
}

const scope = self as unknown as WorkerScope

/** Abort controllers of the jobs in flight, by job id. */
const jobs = new Map<string, AbortController>()

/** The code for an error the pipeline did not classify itself: a withdrawn
 *  grant is 'permission'; a drive that stopped answering is 'read'; anything
 *  else is what the phase was doing (the pre-pass only reads). */
function codeFor(err: unknown, phase: ConvertPhase): ConversionErrorCode {
  if (err instanceof ConvertError) return err.code
  if (isPermissionError(err)) return 'permission'
  const n = errorName(err)
  if (n === 'NotReadableError' || n === 'NotFoundError') return 'read'
  return phase === 'prepass' ? 'read' : 'write'
}

async function convert(msg: Extract<MainToWorker, { type: 'convert' }>): Promise<void> {
  const ctrl = new AbortController()
  jobs.set(msg.jobId, ctrl)
  let phase: ConvertPhase = 'prepass'
  try {
    const file = await msg.raw.getFile()
    const src = new FileSource(file)
    const out = fsaDir(msg.outDir)
    const result = await convertAndSummarize(src, out, {
      stem: msg.stem,
      sourceFile: msg.sourceFile,
      readChunkBytes: STORAGE_READ_CHUNK_BYTES,
      writeChunkBytes: STORAGE_CSV_WRITE_CHUNK_BYTES,
      signal: ctrl.signal,
      onProgress: (p: ConvertProgress) => {
        phase = p.phase
        scope.postMessage({
          type: 'progress',
          jobId: msg.jobId,
          phase: p.phase,
          bytesDone: p.bytesDone,
          bytesTotal: p.bytesTotal,
          rows: p.rows,
        })
      },
      placement: (sensorId: number) => msg.placement.labels[sensorId] ?? msg.placement.fallback,
      fCutHz: STORAGE_NOISE_F_CUT_HZ,
      gapUs: STORAGE_GAP_US,
      tsOutlierUs: STORAGE_TS_OUTLIER_US,
    })
    scope.postMessage({ type: 'done', jobId: msg.jobId, meta: result.meta, summary: result.summary, outputs: result.outputs })
  } catch (err) {
    const aborted = ctrl.signal.aborted || (err instanceof ConvertError && err.code === 'aborted')
    if (aborted) scope.postMessage({ type: 'cancelled', jobId: msg.jobId })
    else scope.postMessage({ type: 'failed', jobId: msg.jobId, code: codeFor(err, phase), detail: errorDetail(err) })
  } finally {
    jobs.delete(msg.jobId)
  }
}

scope.onmessage = (ev) => {
  const msg = ev.data
  switch (msg.type) {
    case 'convert':
      void convert(msg)
      return
    case 'cancel':
      // handled between the pipeline's awaited reads and writes: the abort
      // surfaces there and the catch above answers 'cancelled'
      jobs.get(msg.jobId)?.abort()
      return
  }
}
