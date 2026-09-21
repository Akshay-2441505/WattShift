import type { DisplayState, SiteJob, SiteView } from '../types'

/**
 * The recorded five-minute demo run (docs/demo/transcript.txt), replayed on the front page. These times and costs
 * are what the real Slurm, agent and cloud produced; the tariff clock was a time-lapse (2 minutes = 1 tariff hour).
 * Job end times are approximate (the jobs ran about 40 seconds each).
 */
const at = (hms: string) => new Date(`2026-09-20T${hms}+05:30`).toISOString()

const CHEAP_FROM = '19:54:00'

interface RecordedJob {
  ref: string
  gpus: number
  would: string
  started: string
  ended: string
  baseline: number
  actual: number
}

export const RECORDED: RecordedJob[] = [
  { ref: '8', gpus: 2, would: '19:50:19', started: '19:54:04', ended: '19:54:44', baseline: 0.44, actual: 0.3 },
  { ref: '9', gpus: 1, would: '19:50:19', started: '19:54:04', ended: '19:54:44', baseline: 0.22, actual: 0.15 },
  { ref: '10', gpus: 1, would: '19:52:00', started: '19:54:44', ended: '19:55:24', baseline: 0.22, actual: 0.15 },
]

export const RECORDED_SUMMARY = { baseline: 0.88, actual: 0.6, saved: 0.28, pct: 31.99, startLag: '4 seconds' }

export interface DemoStep {
  now: string
  caption: string
  wattshiftOff?: boolean
  mode: 'shadow' | 'autonomous'
  show: (r: RecordedJob) => { display: DisplayState; planned?: boolean; applied?: boolean; running?: boolean; done?: boolean }
}

export const STEPS: DemoStep[] = [
  {
    now: '19:48:40', mode: 'shadow',
    caption: 'Three jobs are waiting behind a busy cluster. Electricity is expensive now and turns cheap at 19:54:00.',
    show: () => ({ display: 'seen' }),
  },
  {
    now: '19:49:40', mode: 'shadow',
    caption: 'Shadow mode: Wattshift plans a start time for each job and shows it. Nothing in Slurm changes.',
    show: () => ({ display: 'would_hold', planned: true }),
  },
  {
    now: '19:50:40', mode: 'autonomous',
    caption: 'Autonomous mode: it sets a start time of 19:54:00 on each job. The GPUs are free, but Slurm holds the jobs.',
    show: () => ({ display: 'held', planned: true, applied: true }),
  },
  {
    now: '19:53:10', mode: 'autonomous', wattshiftOff: true,
    caption: 'Now Wattshift is switched off completely. Slurm still keeps the start times.',
    show: () => ({ display: 'held', planned: true, applied: true }),
  },
  {
    now: '19:54:20', mode: 'autonomous', wattshiftOff: true,
    caption: 'At 19:54:04 Slurm starts the jobs by itself, four seconds after the set time. Nothing of ours is running.',
    show: (r) => (r.ref === '10' ? { display: 'seen', planned: true, applied: true } : { display: 'running', planned: true, applied: true, running: true }),
  },
  {
    now: '19:55:50', mode: 'autonomous',
    caption: 'Wattshift comes back, reads Slurm’s own records, and measures the saving: ₹0.28, 32% lower than starting when Slurm would have.',
    show: () => ({ display: 'done', planned: true, applied: true, running: true, done: true }),
  },
]

/** The recorded run at one step, in the same shape the live page gets from the cloud, so the same components draw both. */
export function demoView(step: number): SiteView {
  const s = STEPS[step]
  const jobs: SiteJob[] = RECORDED.map((r) => {
    const v = s.show(r)
    return {
      ref: r.ref, state: v.done ? 'COMPLETED' : v.running ? 'RUNNING' : 'PENDING', plan_status: v.applied ? 'planned' : 'pending',
      display: v.display, note: null, gpus: r.gpus, time_limit_min: 1, max_wait_min: 1440, submit_time: at('19:48:00'),
      baseline_start: at(r.would), planned_start: v.planned ? at(CHEAP_FROM) : null, applied_start: v.applied ? at(CHEAP_FROM) : null,
      actual_start: v.running ? at(r.started) : null, actual_end: v.done ? at(r.ended) : null,
      baseline_cost: r.baseline, planned_cost: v.planned ? r.actual : null, actual_cost: v.done ? r.actual : null,
      saved: v.done ? +(r.baseline - r.actual).toFixed(2) : null,
    }
  })
  const finished = jobs.filter((j) => j.display === 'done')
  const counts: SiteView['summary']['counts'] = {}
  for (const j of jobs) counts[j.display] = (counts[j.display] ?? 0) + 1
  return {
    site: {
      id: 'recorded', company: 'Recorded', name: 'demo run', mode: s.mode, release_all: false, gpus: 8,
      last_seen_at: s.wattshiftOff ? at('19:51:00') : at(s.now), agent_version: '0.1.0', agent_mode: s.mode, tariff: 'demo tariff',
    },
    now: at(s.now),
    summary: {
      saved: finished.length ? RECORDED_SUMMARY.saved : 0, baseline: finished.length ? RECORDED_SUMMARY.baseline : 0,
      pct_saved: finished.length ? RECORDED_SUMMARY.pct : null, jobs_measured: finished.length,
      potential: finished.length ? 0 : jobs.some((j) => j.planned_cost != null) ? RECORDED_SUMMARY.saved : 0, counts,
    },
    jobs,
    activity: [],
    zones: [
      { start: at('19:48:00'), end: at(CHEAP_FROM), zone: 'peak' },
      { start: at(CHEAP_FROM), end: at('19:56:00'), zone: 'solar' },
    ],
  }
}
