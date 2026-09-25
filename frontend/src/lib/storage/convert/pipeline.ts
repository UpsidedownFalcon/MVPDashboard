// One raw log in, three outputs out (plan 4.2): convertLog (convert.ts:
// CSV + meta.json) runs with a StatsSink attached, then the summary is
// rendered from the sink and written as `<stem>_summary.txt`. The worker
// calls this once per file; the page shows the returned text (decision P).
// A summary write that fails is aborted (no partial file) and surfaces as
// ConvertError 'write', like the decoder's own output errors.
import { errorDetail, type ByteSink, type ByteSource, type DirLike } from '../io'
import { convertLog } from './convert'
import { StatsSink } from './stats'
import { renderSummary } from './summary'
import { ConvertError, type PipelineOptions, type PipelineResult } from './types'

export async function convertAndSummarize(src: ByteSource, out: DirLike, opts: PipelineOptions): Promise<PipelineResult> {
  const { placement, fCutHz, gapUs, tsOutlierUs, ...convertOpts } = opts
  const sink = new StatsSink({ fCutHz, gapUs, tsOutlierUs })
  const result = await convertLog(src, out, { ...convertOpts, sink })
  const stats = sink.finish()
  const summary = renderSummary({ csvName: result.outputs.csv, meta: result.meta, stats, placement, fCutHz, gapUs })
  if (opts.signal?.aborted) throw new ConvertError('aborted', 'aborted before the summary was written')
  await writeText(out, result.outputs.summary, summary)
  return { meta: result.meta, summary, outputs: result.outputs }
}

/** Write a whole UTF-8 text file through the DirLike seam. */
async function writeText(out: DirLike, name: string, text: string): Promise<void> {
  let sink: ByteSink
  try {
    sink = await out.create(name)
  } catch (err) {
    throw new ConvertError('write', `${name}: ${errorDetail(err)}`)
  }
  try {
    await sink.write(new TextEncoder().encode(text))
    await sink.close()
  } catch (err) {
    await sink.abort().catch(() => undefined)
    throw new ConvertError('write', `${name}: ${errorDetail(err)}`)
  }
}
