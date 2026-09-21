import { describe, expect, it } from 'vitest'
import { STATE, agentStatus, countdown, jobLine, timelineLayout } from './live'
import type { DisplayState, SiteJob, SiteView } from '../types'

const T0 = Date.UTC(2026, 8, 21, 10, 0, 0) // 15:30:00 IST
const at = (s: number) => new Date(T0 + s * 1000).toISOString()

const job = (over: Partial<SiteJob>): SiteJob => ({
  ref: '1', state: 'PENDING', plan_status: 'planned', display: 'held', note: null, gpus: 4, time_limit_min: 1, max_wait_min: 180,
  submit_time: at(0), baseline_start: null, planned_start: null, applied_start: null, actual_start: null, actual_end: null,
  baseline_cost: null, planned_cost: null, actual_cost: null, saved: null, ...over,
})

const view = (over: Partial<SiteView> = {}, site: Partial<SiteView['site']> = {}): SiteView => ({
  site: { id: 's', company: 'Acme', name: 'Pune-1', mode: 'autonomous', release_all: false, gpus: 8, last_seen_at: at(-5), agent_version: '0.1.0', agent_mode: 'autonomous', tariff: 'X', ...site },
  now: at(0),
  summary: { saved: 0, baseline: 0, pct_saved: null, jobs_measured: 0, potential: 0, counts: {} },
  jobs: [], activity: [],
  zones: [{ start: at(-60), end: at(120), zone: 'peak' }, { start: at(120), end: at(300), zone: 'solar' }],
  ...over,
})

describe('state labels', () => {
  it('has a label and a tone for every state the API can send', () => {
    const all: DisplayState[] = ['seen', 'would_hold', 'held', 'runs_normally', 'no_window', 'skipped', 'owner_changed', 'left_alone', 'released', 'running', 'done', 'ended']
    for (const s of all) expect(STATE[s].label.length).toBeGreaterThan(2)
  })
})

describe('countdown', () => {
  it('counts down, then up', () => {
    expect(countdown(at(0), at(125))).toBe('in 2m 05s')
    expect(countdown(at(0), at(45))).toBe('in 45s')
    expect(countdown(at(0), at(0))).toBe('now')
    expect(countdown(at(0), at(-30))).toBe('30s ago')
    expect(countdown(at(0), at(-200))).toBe('3m 20s ago')
  })
})

describe('job line', () => {
  it('says what is happening in words, with the time of day', () => {
    expect(jobLine(job({ display: 'held', applied_start: at(120) }))).toBe('Held until 15:32:00')
    expect(jobLine(job({ display: 'would_hold', planned_start: at(120) }))).toBe('Would hold until 15:32:00')
    expect(jobLine(job({ display: 'running', actual_start: at(130) }))).toBe('Running since 15:32:10')
    expect(jobLine(job({ display: 'done', saved: 33.76 }))).toBe('Done · saved ₹33.76')
    expect(jobLine(job({ display: 'done', saved: null }))).toBe('Done')
    expect(jobLine(job({ display: 'skipped', note: 'array' }))).toBe('Left alone (array)')
    expect(jobLine(job({ display: 'owner_changed' }))).toBe('Changed by its owner: no longer managed')
  })
})

describe('agent status', () => {
  it('is connected when it reported recently, silent when not, and absent before the first report', () => {
    expect(agentStatus(view())).toEqual({ ok: true, label: 'Agent connected · 5 s ago' })
    expect(agentStatus(view({}, { last_seen_at: at(-200) }))).toEqual({ ok: false, label: 'Agent silent for 3m 20s' })
    expect(agentStatus(view({}, { last_seen_at: null }))).toEqual({ ok: false, label: 'No agent has connected yet' })
  })
})

describe('shift timeline layout', () => {
  it('puts the axis on the zone extent and positions every mark as a fraction of it', () => {
    const v = view({ jobs: [job({ ref: 'a', baseline_start: at(0), applied_start: at(150), planned_start: at(150) })] })
    const L = timelineLayout(v)
    expect(L.t0).toBe(T0 - 60_000) // zones run from -60 s to +300 s: a 360 s axis
    expect(L.nowX).toBeCloseTo(60 / 360)
    expect(L.nowInside).toBe(true)
    expect(L.zones.map((z) => [z.zone, +z.x0.toFixed(3), +z.x1.toFixed(3)])).toEqual([['peak', 0, 0.5], ['solar', 0.5, 1]])
    const r = L.rows[0]
    expect(r.fromX).toBeCloseTo(60 / 360)
    expect(r.toX).toBeCloseTo(210 / 360)
    expect(r.runX0).toBeNull() // no real run yet
  })

  it('leaves the now line out when the axis ends before now', () => {
    const v = view({ jobs: [] })
    expect(timelineLayout({ ...v, now: at(3600) }).nowInside).toBe(false)
  })

  it('draws a run bar once the job has really run, and follows a running job to now', () => {
    const done = timelineLayout(view({ jobs: [job({ baseline_start: at(0), actual_start: at(150), actual_end: at(210), display: 'done' })] })).rows[0]
    expect(done.runX0).toBeCloseTo(210 / 360)
    expect(done.runX1).toBeCloseTo(270 / 360)
    const running = timelineLayout(view({ jobs: [job({ baseline_start: at(0), actual_start: at(-30), display: 'running' })] })).rows[0]
    expect(running.runX1).toBeCloseTo(60 / 360) // up to now
  })

  it('keeps marks inside the axis, skips jobs with nothing to draw, and shows the newest first', () => {
    const L = timelineLayout(view({ jobs: [
      job({ ref: 'none' }),
      job({ ref: 'far', baseline_start: at(-9999), applied_start: at(9999), submit_time: at(-10) }),
      job({ ref: 'new', baseline_start: at(0), applied_start: at(150), submit_time: at(-1) }),
    ] }))
    expect(L.rows.map((r) => r.ref)).toEqual(['new', 'far'])
    expect(L.rows[1].fromX).toBe(0)
    expect(L.rows[1].toX).toBe(1)
  })

  it('still lays out when the tariff is unknown (no zones)', () => {
    const L = timelineLayout(view({ zones: [], jobs: [job({ baseline_start: at(0), applied_start: at(300) })] }))
    expect(L.zones).toEqual([])
    expect(L.t1 - L.t0).toBe(20 * 60_000) // 5 minutes back to 15 minutes ahead
  })
})
