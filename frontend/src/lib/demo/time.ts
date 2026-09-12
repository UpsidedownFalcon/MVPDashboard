/** Millisecond ISO with a Z suffix: byte-identical to the backend's _iso(),
 *  which is what makes the timeline/event-log join on `created_at` exact. */
export function iso(ms: number): string {
  return new Date(ms).toISOString()
}
