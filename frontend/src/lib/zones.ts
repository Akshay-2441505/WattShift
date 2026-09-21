import type { Zone } from '../types'

// Two zones carry colour (cheap green, expensive vermilion); the middle one is deliberately neutral.
// The API's names stay (solar / baseline / peak); the words on screen are the plain ones.
export const ZONE_WORD: Record<Zone, string> = {
  solar: 'Cheap',
  baseline: 'Normal',
  peak: 'Expensive',
}

/** The strip and band styles use these three names. */
export const ZONE_KEY: Record<Zone, 'cheap' | 'normal' | 'peak'> = {
  solar: 'cheap',
  baseline: 'normal',
  peak: 'peak',
}

export const ZONE_ORDER: Zone[] = ['solar', 'baseline', 'peak']

/** The zone in force at an instant, from the zone segments the API sends. */
export function zoneAt(zones: { start: string; end: string; zone: Zone }[], iso: string | null): Zone | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  const z = zones.find((s) => new Date(s.start).getTime() <= t && t < new Date(s.end).getTime())
  return z?.zone ?? null
}
