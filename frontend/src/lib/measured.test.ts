import { describe, expect, it } from 'vitest'
import { fleetMonthly, fmtWatts, headline, replayNote, runWord } from './measured'
import type { Measured, MeasuredRun } from '../types'

const run = (o: Partial<MeasuredRun> = {}): MeasuredRun => ({ status: 'done', started_at: '2026-07-01T13:30:00Z', planned_start: null, zone: 'peak', avg_watts: 68, energy_wh: 0.57, rs_per_gpu_hour: 0.72, ...o })
const summary = (o: Partial<Measured['summary']> = {}): Measured['summary'] => ({
  pairs: 2, simulated_pairs: 0, waiting: 0, avg_watts_without: 68, avg_watts_with: 67, rs_per_gpu_hour_without: 0.72, rs_per_gpu_hour_with: 0.49, saved_rs_per_gpu_hour: 0.23, pct_saved: 31.9, ...o,
})

describe('measured power', () => {
  it('scales a per-GPU-hour saving to a fleet over a 30-day month', () => {
    expect(fleetMonthly(0.23, 100, 8)).toBeCloseTo(0.23 * 100 * 8 * 30)
    expect(fleetMonthly(0.23, 0, 8)).toBe(0)
  })

  it('says in one line what a run is doing', () => {
    expect(runWord(run())).toBe('68 W')
    expect(runWord(run({ avg_watts: null, rs_per_gpu_hour: null }))).toBe('Finished, no reading')
    expect(runWord(run({ status: 'running', avg_watts: null }))).toBe('Running')
    expect(runWord(run({ status: 'failed', avg_watts: null }))).toBe('Failed')
    expect(runWord(run({ status: 'scheduled', avg_watts: null, planned_start: '2026-07-02T06:30:00Z' }))).toMatch(/^Waiting for /)
    expect(runWord(run({ status: 'queued', avg_watts: null }))).toBe('Waiting for a window')
  })

  it('rounds watts to one decimal', () => {
    expect(fmtWatts(67.565)).toBe('67.6 W')
    expect(fmtWatts(67)).toBe('67 W')
  })

  it('says when pairs were made on the replay clock', () => {
    expect(replayNote(summary())).toBeNull()
    expect(replayNote(summary({ simulated_pairs: 2 }))).toMatch(/^All 2 pairs were run on the replay clock/)
    expect(replayNote(summary({ simulated_pairs: 1 }))).toMatch(/^1 of the 2 pairs were run on the replay clock/)
    expect(replayNote(summary({ pairs: 1, simulated_pairs: 1 }))).toMatch(/^The pair was run/)
  })

  it('states the finding, and says plainly when nothing was saved', () => {
    expect(headline(summary(), 'Tesla T4')).toBe('On a Tesla T4, one GPU-hour costs ₹0.72 started at once and ₹0.49 at the hour Wattshift chose: 31.9% less.')
    expect(headline(summary({ rs_per_gpu_hour_with: 0.72, saved_rs_per_gpu_hour: 0, pct_saved: 0 }), 'Tesla T4')).toMatch(/nothing was saved/)
    expect(headline(summary({ rs_per_gpu_hour_with: 0.8, saved_rs_per_gpu_hour: -0.08, pct_saved: -11.1 }), 'Tesla T4')).toMatch(/cost more/)
    expect(headline(summary({ pairs: 0, pct_saved: null, saved_rs_per_gpu_hour: null }), 'Tesla T4')).toBeNull()
  })
})
