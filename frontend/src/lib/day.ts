import type { Zone } from '../types'
import { rateAt, type Band } from './tariff'

/** 9 -> "9 am", 12 -> "12 pm", 0 or 24 -> "12 am", 17.5 -> "5:30 pm". Plain times for readers who do not think in 24 hours. */
export function clock12(hour: number): string {
  const total = Math.round((((hour % 24) + 24) % 24) * 60)
  const h = Math.floor(total / 60) % 24
  const m = total % 60
  const suffix = h < 12 ? 'am' : 'pm'
  const h12 = h % 12 === 0 ? 12 : h % 12
  return m === 0 ? `${h12} ${suffix}` : `${h12}:${String(m).padStart(2, '0')} ${suffix}`
}

/** What each tariff band is called on the picture. In Maharashtra these three line up with night, daytime and evening. */
export const BAND_WORD: Record<Zone, string> = { baseline: 'Night', solar: 'Daytime', peak: 'Evening' }
export const PRICE_WORD: Record<Zone, string> = { baseline: 'normal', solar: 'cheap', peak: 'expensive' }

/** The picture is drawn in a fixed 640 x 405 box and scaled by the browser. */
export const DAY = { W: 640, H: 405, X0: 16, X1: 624, BASE: 350, MAX_H: 190, TOKEN_H: 26, TOKEN_W: 34, GAP: 4 } as const

export const hourX = (h: number) => DAY.X0 + (h / 24) * (DAY.X1 - DAY.X0)
export const hourWidth = () => hourX(1) - hourX(0)

/** Block heights scale to the dearest band, so the picture always fills its space and the ratios are true. */
export function rateScale(bands: Band[], base: number): (rate: number) => number {
  const top = Math.max(...bands.map((b) => base * (1 + b.pct / 100)))
  return (rate) => DAY.BASE - (rate / top) * DAY.MAX_H
}

/** Where the top of the block under a given hour is. */
export function blockTop(bands: Band[], base: number, hour: number): number {
  return rateScale(bands, base)(rateAt(bands, base, Math.min(hour, 23.999)))
}

/** The stack of jobs sits just above the block under its start hour. Returns the y of the stack's bottom edge. */
export function stackBottom(bands: Band[], base: number, hour: number): number {
  return blockTop(bands, base, hour) - 5
}
