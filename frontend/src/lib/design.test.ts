import { describe, expect, it } from 'vitest'
import { GLOSSARY } from './glossary'
import { demoView, RECORDED, RECORDED_SUMMARY, STEPS } from './demo'
import { fmtRealSpeed, fmtRealSpeedShort, fmtSpan, isTimelapse, jobWait, timelineFinding, timelineLayout } from './live'
import { clock12, DAY, hourX, blockTop, rateScale } from './day'
import { RACK_GROUPS, stripLook, stripZone } from './strips'
import { bandAt, cheapestStart, dayBands, hourLabel, jobCost, rateAt } from './tariff'
import { zoneAt } from './zones'
import type { DisplayState, TariffRule } from '../types'

const RULES: TariffRule[] = [
  { zone: 'baseline', start_hour: 0, end_hour: 9, season: null, adj_pct: 0 },
  { zone: 'solar', start_hour: 9, end_hour: 17, season: 'apr_sep', adj_pct: -15 },
  { zone: 'solar', start_hour: 9, end_hour: 17, season: 'oct_mar', adj_pct: -25 },
  { zone: 'peak', start_hour: 17, end_hour: 24, season: null, adj_pct: 25 },
]
const BASE = 8.44

describe('tariff maths (MERC HT industrial, FY 2026-27)', () => {
  const summer = dayBands(RULES, 'apr_sep')
  const winter = dayBands(RULES, 'oct_mar')

  it('uses the right solar discount for the season', () => {
    expect(summer.find((b) => b.zone === 'solar')?.pct).toBe(-15)
    expect(winter.find((b) => b.zone === 'solar')?.pct).toBe(-25)
    expect(summer.map((b) => b.zone)).toEqual(['baseline', 'solar', 'peak'])
  })
  it('finds the band and the rate for a time of day, wrapping past midnight', () => {
    expect(bandAt(summer, 20).zone).toBe('peak')
    expect(bandAt(summer, 24).zone).toBe('baseline') // 24:00 is 00:00
    expect(rateAt(summer, BASE, 20)).toBeCloseTo(10.55)
    expect(rateAt(summer, BASE, 12)).toBeCloseTo(7.174)
  })
  it('reproduces the worked example on the front page: Rs 105.50 at 20:00 against Rs 71.74 at noon', () => {
    expect(jobCost(summer, BASE, 20, 1, 10)).toBeCloseTo(105.5, 1)
    expect(jobCost(summer, BASE, 12, 1, 10)).toBeCloseTo(71.74, 1)
    const saved = jobCost(summer, BASE, 20, 1, 10) - jobCost(summer, BASE, 12, 1, 10)
    expect(saved).toBeCloseTo(33.76, 1)
    expect(saved / jobCost(summer, BASE, 20, 1, 10)).toBeCloseTo(0.32, 2)
  })
  it('bills each side of a zone edge at its own rate', () => {
    // 16:30 to 17:30: half an hour solar, half an hour peak
    expect(jobCost(summer, BASE, 16.5, 1, 10)).toBeCloseTo(5 * BASE * 0.85 + 5 * BASE * 1.25, 1)
  })
  it('picks the earliest cheapest quarter hour', () => {
    expect(cheapestStart(summer, BASE, 1, 10)).toBe(9)
    expect(cheapestStart(winter, BASE, 1, 10)).toBe(9)
  })
  it('labels hours', () => {
    expect(hourLabel(9)).toBe('09:00')
    expect(hourLabel(20.25)).toBe('20:15')
    expect(hourLabel(24)).toBe('00:00')
  })
})

describe('strips carry state by shape, not colour alone', () => {
  const ALL: DisplayState[] = ['seen', 'would_hold', 'held', 'runs_normally', 'no_window', 'skipped', 'owner_changed', 'left_alone', 'released', 'running', 'done', 'ended']

  it('gives every state a look', () => {
    for (const s of ALL) expect(stripLook(s).word.length).toBeGreaterThan(0)
  })
  it('puts every state in exactly one rack group, so no job is ever hidden or shown twice', () => {
    for (const s of ALL) expect(RACK_GROUPS.filter((g) => g.states.includes(s))).toHaveLength(1)
  })
  it('makes a held job cocked, a plan dashed, a finished job notched, a released job struck, an owner change doubled', () => {
    expect(stripLook('held').form).toBe('cocked')
    expect(stripLook('would_hold').form).toBe('planned')
    expect(stripLook('done').form).toBe('notched')
    expect(stripLook('released').form).toBe('struck')
    expect(stripLook('owner_changed').form).toBe('doubled')
  })
  it('colours a strip by the zone its start falls in', () => {
    const zones = demoView(2).zones
    const held = demoView(2).jobs[0]
    expect(zoneAt(zones, held.applied_start)).toBe('solar')
    expect(stripZone(held, zones)).toBe('cheap')
    expect(stripZone({ ...held, applied_start: null, planned_start: null, actual_start: null }, zones)).toBe('normal')
  })
})

describe('the recorded demo run on the front page', () => {
  it('adds up to what the demo really measured', () => {
    const baseline = RECORDED.reduce((n, r) => n + r.baseline, 0)
    const actual = RECORDED.reduce((n, r) => n + r.actual, 0)
    expect(baseline).toBeCloseTo(RECORDED_SUMMARY.baseline)
    expect(actual).toBeCloseTo(RECORDED_SUMMARY.actual)
    expect(baseline - actual).toBeCloseTo(RECORDED_SUMMARY.saved)
    expect(((baseline - actual) / baseline) * 100).toBeCloseTo(RECORDED_SUMMARY.pct, 0)
  })
  it('tells the story in order: watching, planned, held, held with Wattshift off, started by Slurm, measured', () => {
    const states = STEPS.map((_, i) => demoView(i).jobs.map((j) => j.display))
    expect(states[0]).toEqual(['seen', 'seen', 'seen'])
    expect(states[1]).toEqual(['would_hold', 'would_hold', 'would_hold'])
    expect(states[2]).toEqual(['held', 'held', 'held'])
    expect(states[3]).toEqual(['held', 'held', 'held'])
    expect(states[4]).toEqual(['running', 'running', 'seen'])
    expect(states[5]).toEqual(['done', 'done', 'done'])
    expect(demoView(5).summary.saved).toBe(0.28)
    expect(demoView(2).summary.saved).toBe(0) // nothing is measured until jobs have run
    expect(demoView(2).summary.potential).toBe(0.28) // the plan is shown separately
  })
  it('never starts a held job before its set time, and the clock only moves forward', () => {
    let last = 0
    STEPS.forEach((_, i) => {
      const v = demoView(i)
      const now = new Date(v.now).getTime()
      expect(now).toBeGreaterThan(last)
      last = now
      for (const j of v.jobs) if (j.actual_start && j.applied_start) expect(new Date(j.actual_start).getTime()).toBeGreaterThanOrEqual(new Date(j.applied_start).getTime())
    })
  })
  it('draws through the same layout the live page uses', () => {
    const L = timelineLayout(demoView(2))
    expect(L.rows).toHaveLength(3)
    expect(L.zones).toHaveLength(2)
    expect(L.nowX).toBeGreaterThan(0)
    expect(L.nowX).toBeLessThan(1)
  })
})

describe('words', () => {
  it('has a unique key for every glossary entry', () => {
    const keys = GLOSSARY.map((g) => g.key)
    expect(new Set(keys).size).toBe(keys.length)
  })

  const sources = import.meta.glob('../**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
  const own = Object.entries(sources).filter(([path]) => !path.endsWith('.test.ts'))

  it('defines every glossary term a screen uses', () => {
    const known = new Set(GLOSSARY.map((g) => g.key))
    const used: string[] = []
    for (const [, src] of own) for (const m of src.matchAll(/<Term k="([a-z]+)"/g)) used.push(m[1])
    expect(used.length).toBeGreaterThan(8)
    expect(used.filter((k) => !known.has(k))).toEqual([])
  })
  it('keeps the em-dash out of everything a visitor reads', () => {
    const offenders = own.filter(([, src]) => /[—–]/.test(src)).map(([path]) => path)
    expect(offenders).toEqual([])
  })
})


describe('plain times and the picture of a day', () => {
  it('writes times the way people say them', () => {
    expect(clock12(9)).toBe('9 am')
    expect(clock12(12)).toBe('12 pm')
    expect(clock12(0)).toBe('12 am')
    expect(clock12(24)).toBe('12 am')
    expect(clock12(17.5)).toBe('5:30 pm')
    expect(clock12(20)).toBe('8 pm')
  })
  const bands = dayBands(RULES, 'apr_sep')
  it('makes block height follow price, with the dearest band the tallest', () => {
    const y = rateScale(bands, 8.44)
    expect(y(8.44 * 1.25)).toBeCloseTo(DAY.BASE - DAY.MAX_H) // the peak fills the space
    expect(y(8.44 * 0.85)).toBeGreaterThan(y(8.44)) // cheap is lower (larger y) than normal
    expect(y(8.44)).toBeGreaterThan(y(8.44 * 1.25))
  })
  it('puts the job stack on the block under its start hour', () => {
    expect(blockTop(bands, 8.44, 20)).toBeLessThan(blockTop(bands, 8.44, 12)) // evening block is taller, so its top is higher up
    expect(hourX(0)).toBe(DAY.X0)
    expect(hourX(24)).toBe(DAY.X1)
    expect(blockTop(bands, 8.44, 24)).toBe(blockTop(bands, 8.44, 23.999)) // a start at 24:00 does not fall off the end
  })
})

describe('the live timeline says what it shows', () => {
  it('formats a wait', () => {
    expect(fmtSpan(45)).toBe('45 s')
    expect(fmtSpan(221)).toBe('3 min 41 s')
    expect(fmtSpan(3720)).toBe('1 h 02 min')
  })
  it('measures each held job from where Slurm would have started it to its start time', () => {
    const [a, , c] = demoView(2).jobs
    expect(jobWait(a)).toBe(221) // 19:50:19 to 19:54:00
    expect(jobWait(c)).toBe(120) // 19:52:00 to 19:54:00
    expect(jobWait(demoView(0).jobs[0])).toBeNull() // nothing planned yet
  })
  it('summarises the picture in one sentence', () => {
    expect(timelineFinding(demoView(2))).toBe('Wattshift moved 3 jobs to a later start time. On average each waits 3 min 7 s for a cheaper slot. In the time-lapse that stands for about 1.6 hours at real speed.')
    expect(timelineFinding(demoView(0))).toBe('No job has been given a later start time yet.')
  })
})


describe('telling the time-lapse from real speed', () => {
  it('turns a demo wait into what it would be at real speed (2 minutes stand for 1 hour)', () => {
    expect(fmtRealSpeed(120)).toBe('1.0 hours')
    expect(fmtRealSpeed(221)).toBe('1.8 hours')
    expect(fmtRealSpeed(1440)).toBe('12.0 hours') // a 24-minute wait is a 12-hour wait
    expect(fmtRealSpeed(40)).toBe('20 min')
    expect(fmtRealSpeedShort(221)).toBe('1.8 h')
    expect(fmtRealSpeedShort(40)).toBe('20 min')
  })
  it('knows the recorded run and the demo tariff are time-lapse, and a normal site is not', () => {
    const v = demoView(2)
    expect(isTimelapse(v)).toBe(true)
    expect(isTimelapse({ ...v, site: { ...v.site, id: 'abc', tariff: 'MSEDCL HT' } })).toBe(false)
    expect(isTimelapse({ ...v, site: { ...v.site, id: 'abc', tariff: 'TEST E2E' } })).toBe(true)
  })
  it('does not add a real-speed sentence for a normal site', () => {
    const v = demoView(2)
    expect(timelineFinding({ ...v, site: { ...v.site, id: 'abc', tariff: 'MSEDCL HT' } })).not.toContain('real speed')
  })
})
