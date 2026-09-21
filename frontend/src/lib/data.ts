import type { Block } from '../types'

const STEP_MS = 15 * 60000

export function fmtInr(n: number, decimals = 0): string {
  const s = new Intl.NumberFormat('en-IN', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(Math.abs(n))
  return `${n < 0 ? '-' : ''}₹${s}`
}

/** The cheapest contiguous run of 15-min blocks covering `minutes` (lowest mean effective rate, earliest on ties). */
export function cheapestWindow(blocks: Block[], minutes: number) {
  const k = Math.ceil(minutes / 15)
  let best: { start: string; end: string; avg: number } | null = null
  for (let i = 0; i + k <= blocks.length; i++) {
    const run = blocks.slice(i, i + k)
    const t0 = new Date(run[0].ts).getTime()
    if (run.some((b, j) => new Date(b.ts).getTime() !== t0 + j * STEP_MS)) continue // a gap in the price data
    const avg = run.reduce((s, b) => s + b.effective_rate, 0) / k
    if (!best || avg < best.avg) best = { start: run[0].ts, end: new Date(t0 + k * STEP_MS).toISOString(), avg }
  }
  return best
}

export function currentBlock(blocks: Block[], nowIso: string): Block | null {
  const now = new Date(nowIso).getTime()
  return blocks.find((b) => {
    const t = new Date(b.ts).getTime()
    return t <= now && now < t + STEP_MS
  }) ?? null
}

/** The first block after the current one that sits in a different tariff zone. */
export function nextTransition(blocks: Block[], nowIso: string): Block | null {
  const cur = currentBlock(blocks, nowIso)
  if (!cur) return null
  const t = new Date(cur.ts).getTime()
  return blocks.find((b) => new Date(b.ts).getTime() > t && b.tod_zone !== cur.tod_zone) ?? null
}
