import type { SiteJob, SiteView } from '../types'
import { fmtInr } from '../lib/data'
import { fmtRealSpeedShort, fmtSpan, jobLine, jobWait, timelineFinding, timelineLayout } from '../lib/live'
import { RACK_GROUPS, stripCost, stripLook, stripTimes, stripZone } from '../lib/strips'
import { fmtHMS } from '../lib/time'
import { ZONE_KEY } from '../lib/zones'
import { DataTable, Swatch } from './ui'

const ZONE_PHRASE = { cheap: 'in cheap hours', peak: 'in expensive hours', normal: 'in normal hours' } as const

/* ---- one job, one strip ------------------------------------------------------------------------------------- */
export function LiveStrip({ job, zones }: { job: SiteJob; zones: SiteView['zones'] }) {
  const look = stripLook(job.display)
  const t = stripTimes(job)
  const cost = stripCost(job)
  return (
    <li className="strip" data-form={look.form} data-zone={stripZone(job, zones)} data-state={job.display}>
      <div>
        <div className="name" translate="no">Job {job.ref}</div>
        <div className="cap">{job.gpus ? `${job.gpus} GPU${job.gpus === 1 ? '' : 's'}` : 'GPUs unknown'}</div>
      </div>
      <div className="min-w-0">
        <div className="strike font-semibold">{jobLine(job)}</div>
        <div className="mt-1 flex flex-wrap gap-x-5 gap-y-0.5">
          <span className="cap">would have started <span className="val strike">{t.would ?? 'not known'}</span></span>
          {t.slot && <span className="cap">{job.actual_start ? 'started' : 'start time'} <span className="val">{t.slot}</span></span>}
          {t.slot && <span className="cap">{ZONE_PHRASE[stripZone(job, zones)]}</span>}
        </div>
      </div>
      <div className="val strike text-right max-sm:text-left">{cost ?? ''}</div>
    </li>
  )
}

/** The job rack: held jobs hang in their own group, so what is waiting and for what is always visible. */
export function JobRack({ view }: { view: SiteView }) {
  if (!view.jobs.length) {
    return <p className="py-2 text-ink-2">No jobs yet. Jobs that match the flex rules appear here within a few seconds of being submitted.</p>
  }
  return (
    <div className="space-y-8">
      {RACK_GROUPS.map((g) => {
        const jobs = view.jobs.filter((j) => g.states.includes(j.display))
        if (!jobs.length) return null
        return (
          <div key={g.key}>
            <h3 className="text-lg">{g.title}</h3>
            <p className="mb-3 text-[15px] text-ink-3">{g.note}</p>
            <ul className="rack" aria-label={g.title}>
              {jobs.map((j) => <LiveStrip key={j.ref} job={j} zones={view.zones} />)}
            </ul>
          </div>
        )
      })}
    </div>
  )
}

export function JobTable({ jobs }: { jobs: SiteJob[] }) {
  const t = (iso: string | null) => (iso ? fmtHMS(iso) : 'none')
  return (
    <DataTable
      head={['Job', 'State', 'GPUs', 'Would have started', 'Starts or started', 'Cost if not moved', 'Actual or planned']}
      numeric={[2, 3, 4, 5, 6]}
      empty="No jobs yet."
      rows={jobs.map((j) => [
        <span translate="no">Job {j.ref}</span>,
        jobLine(j),
        j.gpus ?? 'unknown',
        t(j.baseline_start),
        t(j.actual_start ?? j.applied_start ?? j.planned_start),
        j.baseline_cost != null ? fmtInr(j.baseline_cost, 2) : 'none',
        j.actual_cost != null ? fmtInr(j.actual_cost, 2) : j.planned_cost != null ? `${fmtInr(j.planned_cost, 2)} planned` : 'none',
      ])}
    />
  )
}

/* ---- the shared time ruler ---------------------------------------------------------------------------------- */
const HDR = 30

const BAND_NAME = { solar: 'Cheap hours', baseline: 'Normal hours', peak: 'Expensive hours' } as const

export function Timeline({ view, minWidth = 620, ticks = [0, 0.25, 0.5, 0.75, 1], gutter = 150, timelapse = false }: { view: SiteView; minWidth?: number; ticks?: number[]; gutter?: number; timelapse?: boolean }) {
  const ROW = timelapse ? 66 : 52
  const L = timelineLayout(view)
  const nowInside = L.nowInside
  const at = (f: number) => fmtHMS(new Date(L.t0 + f * (L.t1 - L.t0)).toISOString())
  const pc = (f: number) => `${(f * 100).toFixed(3)}%`
  const rows = L.rows
  const height = HDR + Math.max(rows.length, 1) * ROW
  const byRef = new Map(view.jobs.map((j) => [j.ref, j]))
  return (
    <div className="overflow-x-auto pb-1">
      <div
        role="img"
        aria-label={`Timeline of ${rows.length} jobs across the price bands. ${timelineFinding(view)} A table version is available. Time runs left to right.`}
        className="grid"
        style={{ gridTemplateColumns: `${gutter}px minmax(0,1fr)`, minWidth }}
      >
        <div style={{ paddingTop: HDR }}>
          {rows.map((r) => {
            const job = byRef.get(r.ref)
            const wait = job ? jobWait(job) : null
            return (
              <div key={r.ref} className="flex flex-col justify-center pr-3" style={{ height: ROW }} translate="no">
                <span className="text-[16px] font-bold leading-tight">Job {r.ref}</span>
                {wait !== null && <span className="text-[14px] leading-tight text-ink-2">{job?.actual_start ? 'waited' : 'waits'} {fmtSpan(wait)}</span>}
                {wait !== null && timelapse && <span className="text-[13px] leading-tight text-ink-3">{fmtRealSpeedShort(wait)} at real speed</span>}
              </div>
            )
          })}
        </div>
        <div className="ruler overflow-hidden" style={{ height }}>
          {L.zones.map((z, i) => (
            <div key={i} className={`absolute inset-y-0 band-${ZONE_KEY[z.zone]}`} style={{ left: pc(z.x0), width: pc(Math.max(0, z.x1 - z.x0)) }}>
              {z.x1 - z.x0 > 0.13 && <span className={`band-${ZONE_KEY[z.zone]} absolute left-1 top-1 z-10 px-1 text-[15px] font-bold text-ink`}>{BAND_NAME[z.zone]}</span>}
            </div>
          ))}
          {[0.25, 0.5, 0.75].map((f) => (
            <div key={f} className="absolute inset-y-0 w-px bg-line" style={{ left: pc(f) }} />
          ))}
          {rows.map((r, i) => {
            const waitEnd = r.runX0 ?? r.toX
            return (
              <div key={r.ref} className="absolute inset-x-0" style={{ top: HDR + i * ROW, height: ROW }}>
                {r.fromX != null && waitEnd != null && waitEnd - r.fromX > 0.004 && (
                  <div className="hatch absolute top-1/2 h-3 -translate-y-1/2 text-ink-2" style={{ left: pc(r.fromX), width: pc(waitEnd - r.fromX) }} />
                )}
                {r.runX0 != null && r.runX1 != null && (
                  <div className="absolute top-1/2 h-5 -translate-y-1/2 bg-ink" style={{ left: pc(r.runX0), width: `max(6px, ${pc(r.runX1 - r.runX0)})` }} />
                )}
                {r.fromX != null && <div className="absolute top-1/2 size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-ink bg-strip" style={{ left: pc(r.fromX) }} />}
                {r.toX != null && r.runX0 == null && <div className="absolute top-1/2 size-4 -translate-x-1/2 -translate-y-1/2 rounded-full bg-ink" style={{ left: pc(r.toX) }} />}
              </div>
            )
          })}
          {nowInside && <div className="absolute inset-y-0 z-0 w-0.5 bg-ink" style={{ left: pc(L.nowX) }} />}
        </div>
        <div />
        <div className="relative h-12">
          {ticks.map((f) => (
            <span key={f} className="num absolute top-1.5 text-[14px] text-ink-2" style={{ left: pc(f), transform: `translateX(${f === 0 ? '0' : f === 1 ? '-100%' : '-50%'})` }}>
              {at(f)}
            </span>
          ))}
          {nowInside && (
            <span className="absolute top-7 -translate-x-1/2 bg-ink px-1.5 text-[13px] font-bold text-board" style={{ left: `clamp(16px, ${pc(L.nowX)}, calc(100% - 16px))` }}>
              now
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

export function TimelineKey() {
  return (
    <ul className="grid gap-x-8 gap-y-2 text-[15px] text-ink-2 sm:grid-cols-2">
      <li className="flex items-center gap-2"><Swatch kind="would" /> Slurm would have started the job here</li>
      <li className="flex items-center gap-2"><Swatch kind="hold" /> the wait: Slurm holds the job</li>
      <li className="flex items-center gap-2"><Swatch kind="start" /> the start time Wattshift set</li>
      <li className="flex items-center gap-2"><Swatch kind="run" /> the job really running</li>
    </ul>
  )
}

export function TimelineTable({ view }: { view: SiteView }) {
  const L = timelineLayout(view)
  const at = (f: number | null) => (f == null ? 'none' : fmtHMS(new Date(L.t0 + f * (L.t1 - L.t0)).toISOString()))
  return (
    <DataTable
      head={['Job', 'Would have started', 'Start time', 'Ran until']}
      numeric={[1, 2, 3]}
      empty="No job has a start time yet."
      rows={L.rows.map((r) => [<span translate="no">Job {r.ref}</span>, at(r.fromX), at(r.toX), at(r.runX1)])}
    />
  )
}
