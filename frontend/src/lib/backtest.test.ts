import { describe, expect, it } from 'vitest'
import { insight, toRequest, validateAssumptions, type Draft } from './backtest'

const rows = [
  { label: 'Under 15 min', jobs_share: 0.644, energy_share: 0.006 },
  { label: '15 min to 1 h', jobs_share: 0.144, energy_share: 0.011 },
  { label: '1 to 3 h', jobs_share: 0.063, energy_share: 0.015 },
  { label: '3 to 12 h', jobs_share: 0.092, energy_share: 0.081 },
  { label: '12 to 24 h', jobs_share: 0.024, energy_share: 0.095 },
  { label: 'Over 24 h', jobs_share: 0.033, energy_share: 0.792 },
]

describe('insight', () => {
  it('finds the job length that uses most of the electricity and how much the scheduler can actually touch', () => {
    const i = insight(rows)
    expect(i.top.label).toBe('Over 24 h')
    expect(i.movableEnergyShare).toBeCloseTo(0.032) // only jobs up to 3 h can be moved
    expect(i.movableJobsShare).toBeCloseTo(0.851)
  })
  it('copes with an empty list', () => {
    expect(insight([]).movableEnergyShare).toBe(0)
  })
})

const draft: Draft = { flexiblePct: '50', slackHours: '12', gpus: '968', kwPerGpu: '1.25', capacityPct: '50' }

describe('assumptions', () => {
  it('accepts the defaults', () => {
    expect(validateAssumptions(draft)).toEqual([])
  })
  it('names each problem in plain words', () => {
    const bad = validateAssumptions({ flexiblePct: '150', slackHours: '-2', gpus: '0', kwPerGpu: 'abc', capacityPct: '0' })
    expect(bad).toHaveLength(5)
    expect(bad.join(' ')).toMatch(/wait.*0 and 100/i)
    expect(bad.join(' ')).toMatch(/hours/i)
    expect(bad.join(' ')).toMatch(/GPUs/)
  })
  it('turns the form into the API request (percent -> share)', () => {
    const r = toRequest({ ...draft, flexiblePct: '25', capacityPct: '80' }, 'sample', null, false)
    expect(r).toEqual({
      source: 'sample',
      retime: false,
      assumptions: { flexible_share: 0.25, slack_hours: 12, kw_per_gpu: 1.25, cluster_gpus: 968, shift_capacity_share: 0.8 },
    })
  })
  it('sends the file text only for uploads', () => {
    expect(toRequest(draft, 'upload', 'a,b\n1,2', true)).toMatchObject({ source: 'upload', csv: 'a,b\n1,2', retime: true })
    expect(toRequest(draft, 'sample', 'ignored', true)).not.toHaveProperty('csv')
  })
})
