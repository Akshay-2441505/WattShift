import type { Measured, MeasuredRun } from '../types'
import { fmtInr } from './data'
import { fmtDayHM } from './time'

export const DAYS_PER_MONTH = 30

/** What a fleet saves in a month at the measured per-GPU-hour saving. Simple arithmetic, shown next to its inputs. */
export const fleetMonthly = (savedPerGpuHour: number, gpus: number, hoursPerDay: number) => savedPerGpuHour * gpus * hoursPerDay * DAYS_PER_MONTH

/** Watts to one decimal: the readings are averages of about 30 samples, so more digits would only look precise. */
export const fmtWatts = (w: number) => `${(Math.round(w * 10) / 10).toString()} W`

/** One short phrase for what a run is doing, or the watts it was measured drawing. */
export function runWord(r: MeasuredRun): string {
  if (r.status === 'done') return r.avg_watts != null ? fmtWatts(r.avg_watts) : 'Finished, no reading'
  if (r.status === 'running') return 'Running'
  if (r.status === 'failed') return 'Failed'
  if (r.planned_start) return `Waiting for ${fmtDayHM(r.planned_start)}`
  if (r.status === 'scheduled') return 'Starting'
  return 'Waiting for a window'
}

/** Which of the pairs were made on the replay clock, in words. Null when none were. */
export function replayNote(s: Measured['summary']): string | null {
  if (s.simulated_pairs === 0) return null
  const which = s.simulated_pairs === s.pairs ? (s.pairs === 1 ? 'The pair was' : `All ${s.pairs} pairs were`) : `${s.simulated_pairs} of the ${s.pairs} pairs were`
  return `${which} run on the replay clock, a sped-up demo clock. The power was measured on a real GPU, but the hour each run was priced at is replayed, not the real hour it ran.`
}

/** The finding as a sentence: says plainly when the hour Wattshift chose was no cheaper. Null until a pair has finished. */
export function headline(s: Measured['summary'], gpu: string): string | null {
  if (s.pairs === 0 || s.rs_per_gpu_hour_without == null || s.rs_per_gpu_hour_with == null || s.pct_saved == null) return null
  const head = `On a ${gpu}, one GPU-hour costs ${fmtInr(s.rs_per_gpu_hour_without, 2)} started at once and ${fmtInr(s.rs_per_gpu_hour_with, 2)} at the hour Wattshift chose`
  if (s.pct_saved > 0) return `${head}: ${s.pct_saved}% less.`
  if (s.pct_saved < 0) return `${head}: ${Math.abs(s.pct_saved)}% more. These jobs arrived in cheap hours, so waiting could not help, and the hour chosen cost more.`
  return `${head}. These jobs arrived in cheap hours already, so nothing was saved.`
}
