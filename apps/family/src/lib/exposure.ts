export type TimeRange = { startMs: number; endMs: number }

/** Pass-through unless the family marked this stretch to be removed. The first segment stays, because it carries the file header. */
export function keepSegment(startMs: number, durationMs: number, removed: TimeRange[], header: boolean): boolean {
  if (header || removed.length === 0) return true
  const end = startMs + durationMs
  return !removed.some((range) => startMs < range.endMs && end > range.startMs)
}

export function overlaps(startMs: number, durationMs: number, removed: TimeRange[]): boolean {
  const end = startMs + durationMs
  return removed.some((range) => startMs < range.endMs && end > range.startMs)
}
