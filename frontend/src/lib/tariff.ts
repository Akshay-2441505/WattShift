import type { TariffRule, Zone } from '../types'

export interface Band {
  zone: Zone
  h0: number
  h1: number
  pct: number
}

/** The tariff's zones for one season as hour ranges of the day, in order. */
export function dayBands(rules: TariffRule[], season: string): Band[] {
  return rules
    .filter((r) => r.season === null || r.season === season)
    .map((r) => ({ zone: r.zone, h0: r.start_hour, h1: r.end_hour, pct: r.adj_pct }))
    .sort((a, b) => a.h0 - b.h0)
}

/** Which band a time of day (0 up to 24, fractional) falls in. */
export function bandAt(bands: Band[], hour: number): Band {
  const h = ((hour % 24) + 24) % 24
  return bands.find((b) => b.h0 <= h && h < b.h1) ?? bands[0]
}

/** Rupees per kWh billed at that time of day. */
export function rateAt(bands: Band[], baseRate: number, hour: number): number {
  return baseRate * (1 + bandAt(bands, hour).pct / 100)
}

/**
 * What a job costs if it starts at `startHour`: kW x hours x the rate of every zone it runs through.
 * Worked minute by minute, so a job that crosses a zone edge pays each side its own rate.
 */
export function jobCost(bands: Band[], baseRate: number, startHour: number, hours: number, kw: number): number {
  const steps = Math.max(1, Math.round(hours * 60))
  let total = 0
  for (let i = 0; i < steps; i++) total += (kw / 60) * rateAt(bands, baseRate, startHour + (i + 0.5) / 60)
  return total
}

/** The start time (hour of day, on the quarter hour) that makes a job of this length cheapest. */
export function cheapestStart(bands: Band[], baseRate: number, hours: number, kw: number): number {
  let best = 0
  let bestCost = Infinity
  for (let q = 0; q < 96; q++) {
    const c = jobCost(bands, baseRate, q / 4, hours, kw)
    if (c < bestCost - 1e-9) {
      best = q / 4
      bestCost = c
    }
  }
  return best
}

export const hourLabel = (h: number) => {
  const total = Math.round(((h % 24) + 24) % 24 * 60)
  return `${String(Math.floor(total / 60) % 24).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}
