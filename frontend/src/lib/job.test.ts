import { describe, expect, it } from 'vitest'
import { deadlineToIso, hoursAhead, istInputValue, tomorrowAt, validateJob } from './job'

const NOW = '2026-09-19T13:30:00Z' // 19:00 IST on Sat 19 Sep

describe('deadline input (always IST, whatever the browser time zone is)', () => {
  it('reads a datetime-local value as IST and returns the UTC instant', () => {
    expect(deadlineToIso('2026-09-20T16:30')).toBe('2026-09-20T11:00:00.000Z')
    expect(deadlineToIso('2026-09-20T00:00')).toBe('2026-09-19T18:30:00.000Z')
  })
  it('rejects empty or malformed input', () => {
    expect(deadlineToIso('')).toBeNull()
    expect(deadlineToIso('tomorrow')).toBeNull()
    expect(deadlineToIso('2026-13-40T99:99')).toBeNull()
  })
  it('formats an instant back as an IST datetime-local value', () => {
    expect(istInputValue('2026-09-20T11:00:00Z')).toBe('2026-09-20T16:30')
    expect(istInputValue(NOW)).toBe('2026-09-19T19:00')
  })
  it('round-trips', () => {
    expect(deadlineToIso(istInputValue(NOW))).toBe(new Date(NOW).toISOString())
  })
})

describe('presets', () => {
  it('adds hours to now', () => {
    expect(hoursAhead(NOW, 6)).toBe('2026-09-20T01:00')
  })
  it('gives tomorrow at a fixed IST hour', () => {
    expect(tomorrowAt(NOW, 12)).toBe('2026-09-20T12:00')
    expect(tomorrowAt(NOW, 16, 30)).toBe('2026-09-20T16:30')
    expect(tomorrowAt('2026-09-19T20:00:00Z', 9)).toBe('2026-09-21T09:00') // 01:30 IST on the 20th -> tomorrow is the 21st
  })
})

describe('validateJob', () => {
  const ok = { durationMin: 60, deadlineLocal: '2026-09-20T16:30', powerKw: 10, nowIso: NOW }
  it('accepts a sensible job', () => {
    expect(validateJob(ok)).toEqual([])
  })
  it('bounds the duration to 1-180 whole minutes', () => {
    expect(validateJob({ ...ok, durationMin: 0 })[0]).toMatch(/1.*180/)
    expect(validateJob({ ...ok, durationMin: 181 })[0]).toMatch(/1.*180/)
    expect(validateJob({ ...ok, durationMin: 12.5 })[0]).toMatch(/whole/)
    expect(validateJob({ ...ok, durationMin: NaN })[0]).toMatch(/1.*180/)
  })
  it('needs a deadline that leaves time to finish the job', () => {
    expect(validateJob({ ...ok, deadlineLocal: '' })[0]).toMatch(/deadline/i)
    // now is 19:00 IST; a 60-minute job with a 19:30 deadline cannot finish
    expect(validateJob({ ...ok, deadlineLocal: '2026-09-19T19:30' })[0]).toMatch(/finish/i)
    expect(validateJob({ ...ok, deadlineLocal: '2026-09-19T20:00' })).toEqual([]) // exactly enough time is fine
  })
  it('bounds power to (0, 1000] kW and allows it to be left blank', () => {
    expect(validateJob({ ...ok, powerKw: 0 })[0]).toMatch(/power/i)
    expect(validateJob({ ...ok, powerKw: 1001 })[0]).toMatch(/power/i)
    expect(validateJob({ ...ok, powerKw: null })).toEqual([])
  })
  it('reports every problem at once', () => {
    expect(validateJob({ durationMin: 0, deadlineLocal: '', powerKw: -1, nowIso: NOW }).length).toBe(3)
  })
})
