import type { DisplayState, SiteJob, SiteView } from '../types'
import { fmtInr } from './data'
import { fmtHMS } from './time'
import { ZONE_KEY, zoneAt } from './zones'

/**
 * A job's state is carried by the SHAPE of its strip, so it never depends on colour alone:
 *   flat = nothing special, dashed = a plan only (nothing changed in Slurm), cocked = held by a start time,
 *   notched = finished, struck = released, doubled = its owner changed it.
 */
export type StripForm = 'flat' | 'planned' | 'cocked' | 'notched' | 'struck' | 'doubled'

export interface StripLook {
  form: StripForm
  word: string // one plain word for the state
}

const LOOK: Record<DisplayState, StripLook> = {
  seen: { form: 'flat', word: 'Waiting' },
  would_hold: { form: 'planned', word: 'Would hold' },
  held: { form: 'cocked', word: 'Held' },
  runs_normally: { form: 'flat', word: 'Runs as normal' },
  no_window: { form: 'flat', word: 'No cheaper slot' },
  skipped: { form: 'flat', word: 'Left alone' },
  owner_changed: { form: 'doubled', word: 'Changed by owner' },
  left_alone: { form: 'flat', word: 'Left alone' },
  released: { form: 'struck', word: 'Released' },
  running: { form: 'flat', word: 'Running' },
  done: { form: 'notched', word: 'Done' },
  ended: { form: 'notched', word: 'Ended' },
}

export const stripLook = (d: DisplayState): StripLook => LOOK[d]

/** Which zone the job starts (or is set to start) in: the colour of its tab. Neutral when unknown. */
export function stripZone(job: SiteJob, zones: SiteView['zones']): 'cheap' | 'normal' | 'peak' {
  const z = zoneAt(zones, job.actual_start ?? job.applied_start ?? job.planned_start)
  return z ? ZONE_KEY[z] : 'normal'
}

/** The three times a strip shows, in one place so the strip and the table agree. */
export function stripTimes(j: SiteJob) {
  return {
    would: j.baseline_start ? fmtHMS(j.baseline_start) : null,
    slot: j.actual_start ?? j.applied_start ?? j.planned_start ? fmtHMS((j.actual_start ?? j.applied_start ?? j.planned_start)!) : null,
  }
}

/** What the job cost against what it would have cost, in words a visitor can read. */
export function stripCost(j: SiteJob): string | null {
  if (j.baseline_cost == null) return null
  if (j.actual_cost != null) return `${fmtInr(j.baseline_cost, 2)} became ${fmtInr(j.actual_cost, 2)}`
  if (j.planned_cost != null) return `${fmtInr(j.baseline_cost, 2)} planned as ${fmtInr(j.planned_cost, 2)}`
  return null
}

/** The groups the job rack shows, in order. A held job hangs in its own group so what is waiting is always visible. */
export const RACK_GROUPS: { key: string; title: string; note: string; states: DisplayState[] }[] = [
  { key: 'held', title: 'Held for a cheaper slot', note: 'Slurm will not start these before their start time.', states: ['held', 'would_hold'] },
  { key: 'active', title: 'Waiting or running', note: 'Not held: Slurm decides when these start.', states: ['seen', 'runs_normally', 'no_window', 'skipped', 'left_alone', 'owner_changed', 'released', 'running'] },
  { key: 'done', title: 'Finished', note: 'The saving below is measured from the real start and end times.', states: ['done', 'ended'] },
]
