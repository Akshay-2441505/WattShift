import { describe, expect, it } from 'vitest'
import { cheapestWindow, currentBlock, fmtInr, nextTransition } from './data'
import { fmtDayHM, fmtHM, istFraction, istHour } from './time'
import type { Block, Zone } from '../types'

const T = (h: number, m = 0) => new Date(Date.UTC(2026, 6, 1, h, m)).toISOString() // 2026-07-01 (a Wednesday) in UTC
const block = (ts: string, zone: Zone, rate: number): Block => ({
  ts, iex_price: rate, tod_zone: zone, tod_multiplier: 1, billed_rs_kwh: 8, effective_rate: rate, carbon_index: 50,
})
const series = (start: Date, rates: number[], zone: Zone = 'solar') =>
  rates.map((r, i) => block(new Date(start.getTime() + i * 15 * 60000).toISOString(), zone, r))

describe('time (IST is UTC+5:30, no DST)', () => {
  it('converts an instant to IST hour and fraction', () => {
    expect(istHour(T(13, 30))).toBe(19) // 13:30Z = 19:00 IST
    expect(istFraction(T(3, 45))).toBeCloseTo(9.25) // 09:15 IST
    expect(istHour(T(18, 30))).toBe(0) // rolls over midnight
  })
  it('formats', () => {
    expect(fmtHM(T(13, 30))).toBe('19:00')
    expect(fmtDayHM(T(13, 30))).toBe('Wed 19:00')
  })
})

describe('forecast helpers', () => {
  const start = new Date(Date.UTC(2026, 6, 1, 6, 0))
  it('finds the cheapest contiguous window, earliest on ties', () => {
    const b = series(start, [9, 9, 3, 3, 3, 3, 9, 3, 3, 3, 3, 9])
    const w = cheapestWindow(b, 60)!
    expect(w.start).toBe(b[2].ts)
    expect(new Date(w.end).getTime() - new Date(w.start).getTime()).toBe(60 * 60000)
    expect(w.avg).toBe(3)
  })
  it('is null when there are too few blocks or a gap breaks every run', () => {
    expect(cheapestWindow(series(start, [1, 2, 3]), 60)).toBeNull()
    const gappy = [...series(start, [1, 1]), ...series(new Date(start.getTime() + 60 * 60000), [1, 1])]
    expect(cheapestWindow(gappy, 60)).toBeNull()
  })
  it('finds the current block and the next zone change', () => {
    const b = [...series(start, [1, 1, 1], 'baseline'), ...series(new Date(start.getTime() + 45 * 60000), [1, 1], 'solar')]
    const now = new Date(start.getTime() + 20 * 60000).toISOString()
    expect(currentBlock(b, now)?.ts).toBe(b[1].ts)
    expect(nextTransition(b, now)?.tod_zone).toBe('solar')
    expect(nextTransition(b, now)?.ts).toBe(b[3].ts)
  })
  it('handles an empty forecast', () => {
    expect(currentBlock([], T(6))).toBeNull()
    expect(nextTransition([], T(6))).toBeNull()
  })
})

describe('money', () => {
  it('formats rupees with Indian grouping', () => {
    expect(fmtInr(123456)).toBe('₹1,23,456')
    expect(fmtInr(1234.5, 2)).toBe('₹1,234.50')
    expect(fmtInr(-28, 0)).toBe('-₹28')
  })
})
