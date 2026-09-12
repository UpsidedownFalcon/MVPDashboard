// Demo-soldier identity (STAGE4 R2). Ids are deliberately, visibly synthetic
// in the address bar ("/device/demo-3") and nowhere else: every other surface
// treats a demo soldier exactly like a real device (user decision 2026-09-12).

export const DEMO_PREFIX = 'demo-'
export const DEMO_COUNT = 5

export function isDemoId(id: string): boolean {
  return id.startsWith(DEMO_PREFIX)
}

/** 0 for a real device (so real devices always sort first), N for `demo-N`,
 *  99 for a malformed demo id so it still lands after every scripted soldier. */
export function demoRank(id: string): number {
  if (!isDemoId(id)) return 0
  const n = Number.parseInt(id.slice(DEMO_PREFIX.length), 10)
  return Number.isFinite(n) && n > 0 ? n : 99
}
