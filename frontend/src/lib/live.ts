import type { DisplayState, SiteJob, SiteView, Zone } from '../types'
import { fmtInr } from './data'
import { fmtHMS } from './time'

export type Tone = 'hold' | 'run' | 'done' | 'muted' | 'warn'

/** One label and one tone per state the API can send. The tone picks the colour and a shape so colour is never alone. */
export const STATE: Record<DisplayState, { label: string; tone: Tone }> = {
  seen: { label: 'Waiting to be planned', tone: 'muted' },
  would_hold: { label: 'Would hold', tone: 'hold' },
  held: { label: 'Held', tone: 'hold' },
  runs_normally: { label: 'Runs as normal', tone: 'muted' },
  no_window: { label: 'No cheaper window', tone: 'muted' },
  skipped: { label: 'Left alone', tone: 'muted' },
  owner_changed: { label: 'Changed by owner', tone: 'warn' },
  left_alone: { label: 'Left alone', tone: 'warn' },
  released: { label: 'Released', tone: 'run' },
  running: { label: 'Running', tone: 'run' },
  done: { label: 'Done', tone: 'done' },
  ended: { label: 'Ended', tone: 'muted' },
}

const pad = (n: number) => String(n).padStart(2, '0')

/** "in 2m 05s", "45s ago", "now": seconds resolution, because the demo runs in minutes. */
export function countdown(nowIso: string, targetIso: string): string {
  const s = Math.round((new Date(targetIso).getTime() - new Date(nowIso).getTime()) / 1000)
  if (s === 0) return 'now'
  const a = Math.abs(s)
  const text = a >= 60 ? `${Math.floor(a / 60)}m ${pad(a % 60)}s` : `${a}s`
  return s > 0 ? `in ${text}` : `${text} ago`
}

/** What is happening to a job, in words. */
export function jobLine(j: SiteJob): string {
  switch (j.display) {
    case 'held': return `Held until ${fmtHMS(j.applied_start ?? j.planned_start!)}`
    case 'would_hold': return `Would hold until ${fmtHMS(j.planned_start!)}`
    case 'running': return `Running since ${fmtHMS(j.actual_start!)}`
    case 'done': return j.saved != null ? `Done · saved ${fmtInr(j.saved, 2)}` : 'Done'
    case 'skipped': return `Left alone${j.note ? ` (${j.note})` : ''}`
    case 'owner_changed': return 'Changed by its owner: no longer managed'
    case 'left_alone': return `Left alone${j.note ? ` (${j.note.replace(/^apply_failed: /, 'could not change it: ')})` : ''}`
    case 'released': return 'Released: starts as soon as Slurm can'
    case 'runs_normally': return 'Not worth moving: runs as Slurm decides'
    case 'no_window': return 'No cheaper window before its limit'
    case 'ended': return `Ended (${j.state.toLowerCase()})`
    default: return 'Waiting to be planned'
  }
}

const SILENT_AFTER_S = 90 // the agent reports every 10-30 s

export function agentStatus(v: SiteView): { ok: boolean; label: string } {
  if (!v.site.last_seen_at) return { ok: false, label: 'No agent has connected yet' }
  const age = Math.round((new Date(v.now).getTime() - new Date(v.site.last_seen_at).getTime()) / 1000)
  if (age <= SILENT_AFTER_S) return { ok: true, label: `Agent connected · ${age} s ago` }
  const a = countdown(v.now, v.site.last_seen_at) // "3m 20s ago": the report is in the past
  return { ok: false, label: `Agent silent for ${a.replace(' ago', '')}` }
}

export interface TimelineRow {
  ref: string
  display: DisplayState
  fromX: number | null // where it would have started
  toX: number | null // where it starts (set) or started (real)
  runX0: number | null // the real run, once there is one
  runX1: number | null
}

export interface TimelineLayout {
  t0: number
  t1: number
  nowX: number
  /** False when the axis ends before now (a demo left idle): the now line is then not drawn. */
  nowInside: boolean
  zones: { x0: number; x1: number; zone: Zone }[]
  rows: TimelineRow[]
}

const MAX_ROWS = 8

/** Everything the shift timeline draws, as fractions (0-1) of its time axis. The axis is the tariff zone extent the API sent. */
export function timelineLayout(v: SiteView): TimelineLayout {
  const now = new Date(v.now).getTime()
  const ms = (iso: string | null) => (iso ? new Date(iso).getTime() : null)
  const t0 = v.zones.length ? new Date(v.zones[0].start).getTime() : now - 5 * 60_000
  const t1 = v.zones.length ? new Date(v.zones[v.zones.length - 1].end).getTime() : now + 15 * 60_000
  const x = (t: number) => Math.min(1, Math.max(0, (t - t0) / (t1 - t0)))
  const opt = (t: number | null) => (t == null ? null : x(t))

  const rows: TimelineRow[] = v.jobs
    .filter((j) => j.baseline_start || j.applied_start || j.planned_start || j.actual_start)
    .sort((a, b) => (a.submit_time < b.submit_time ? 1 : a.submit_time > b.submit_time ? -1 : 0))
    .slice(0, MAX_ROWS)
    .map((j) => {
      const start = ms(j.actual_start)
      const to = start ?? ms(j.applied_start) ?? ms(j.planned_start)
      const end = ms(j.actual_end) ?? (j.display === 'running' ? now : null)
      return {
        ref: j.ref, display: j.display, fromX: opt(ms(j.baseline_start)), toX: opt(to),
        runX0: start != null && end != null ? x(start) : null, runX1: start != null && end != null ? x(end) : null,
      }
    })

  return {
    t0, t1, nowX: x(now), nowInside: now >= t0 && now <= t1, rows,
    zones: v.zones.map((z) => ({ x0: x(new Date(z.start).getTime()), x1: x(new Date(z.end).getTime()), zone: z.zone })),
  }
}

/** 221 -> "3 min 41 s", 45 -> "45 s", 3720 -> "1 h 02 min". */
export function fmtSpan(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} s`
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`
  return `${Math.floor(s / 3600)} h ${pad(Math.floor((s % 3600) / 60))} min`
}

/** How long a job waits (or waited) for its slot: from where Slurm would have started it to where it starts. Null if it does not wait. */
export function jobWait(j: SiteJob): number | null {
  const target = j.actual_start ?? j.applied_start ?? j.planned_start
  if (!j.baseline_start || !target) return null
  const s = (new Date(target).getTime() - new Date(j.baseline_start).getTime()) / 1000
  return s > 1 ? s : null
}

/** In the time-lapse demo the tariff clock runs 60 times faster than life: 2 minutes stand for 1 tariff hour. */
export const LAPSE_SECONDS_PER_HOUR = 120

/** The demo cloud uses a synthetic tariff (its name starts with TEST); the recorded run is a demo too. */
export const isTimelapse = (v: SiteView) => v.site.id === 'recorded' || v.site.tariff.startsWith('TEST')

/** What a wait seen in the time-lapse would be at real speed: 221 s -> "1.8 hours", 40 s -> "20 min". */
export function fmtRealSpeed(seconds: number): string {
  const h = seconds / LAPSE_SECONDS_PER_HOUR
  return h < 1 ? `${Math.round(h * 60)} min` : `${h.toFixed(1)} hours`
}

/** The same, short enough for a label: 221 -> "1.8 h", 40 -> "20 min". */
export function fmtRealSpeedShort(seconds: number): string {
  const h = seconds / LAPSE_SECONDS_PER_HOUR
  return h < 1 ? `${Math.round(h * 60)} min` : `${h.toFixed(1)} h`
}

/** One sentence that says what the timeline shows, so the picture does not have to be decoded. */
export function timelineFinding(v: SiteView): string {
  const waits = v.jobs.map(jobWait).filter((s): s is number => s !== null)
  if (!waits.length) return 'No job has been given a later start time yet.'
  const avg = waits.reduce((a, b) => a + b, 0) / waits.length
  const n = waits.length
  const lapse = isTimelapse(v) ? ` In the time-lapse that stands for about ${fmtRealSpeed(avg)} at real speed.` : ''
  return `Wattshift moved ${n} job${n === 1 ? '' : 's'} to a later start time. ${n === 1 ? 'It waits' : 'On average each waits'} ${fmtSpan(avg)} for a cheaper slot.${lapse}`
}
