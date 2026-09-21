import { useState } from 'react'
import { useSitePoll, useSites } from '../useSitePoll'
import { JobRack, JobTable, Timeline, TimelineKey, TimelineTable } from '../components/board'
import { HeroBoard } from '../components/HeroBoard'
import { LiveControls } from '../components/LiveControls'
import { Notice, PageHead, Section, SpeedNote, Term, ViewToggle } from '../components/ui'
import { fmtInr } from '../lib/data'
import { isTimelapse, timelineFinding } from '../lib/live'
import { fmtHMS } from '../lib/time'
import type { DisplayState, SiteView } from '../types'

const Caption = ({ lapse = true }: { lapse?: boolean }) => (
  <>
    <PageHead title="Live demo">
      This page watches a real job queue (<Term k="slurm">Slurm</Term>) as Wattshift works on it.
    </PageHead>
    {lapse ? (
      <SpeedNote kind="timelapse" title="Time-lapse.">
        The jobs, Slurm and the start times are real, but the tariff clock runs 60 times faster than life: every 2 minutes here stand for 1 hour. For example, a 4-minute wait here would be 2 hours at real speed. See <Term k="timelapse">time-lapse</Term>.
      </SpeedNote>
    ) : (
      <SpeedNote kind="real" title="Real speed.">This cluster is on the real clock and the real tariff.</SpeedNote>
    )}
  </>
)

/** Nothing connected: show the recorded run instead of an empty page. */
function Recorded({ why }: { why: string }) {
  return (
    <>
      <PageHead title="Live demo">
        {why} So this replays a recorded run: three real jobs on a real <Term k="slurm">Slurm</Term>.
      </PageHead>
      <SpeedNote kind="timelapse" title="Time-lapse.">
        The jobs, Slurm and the start times are real, but the tariff clock ran 60 times faster than life: every 2 minutes stand for 1 hour. See <Term k="timelapse">time-lapse</Term>.
      </SpeedNote>
      <div className="pb-6 pt-2">
        <HeroBoard />
      </div>
      <p className="max-w-[62ch] pb-16 text-[15px] text-ink-3">
        For developers: start the live version with <span className="num">python e2e\demo.py</span> and this page shows the same thing as it happens, with working buttons.
      </p>
    </>
  )
}

function Tally({ view }: { view: SiteView }) {
  const s = view.summary
  const n = (k: DisplayState) => s.counts[k] ?? 0
  const measured = s.jobs_measured > 0
  const rows: [string, string][] = [
    ['Planned, not run yet', fmtInr(s.potential, 2)],
    ['Held', String(n('held') + n('would_hold'))],
    ['Running', String(n('running'))],
    ['Done', String(n('done'))],
  ]
  return (
    <div>
      {measured ? (
        <p className="text-[1.25rem] leading-snug" role="status">
          Measured saving <strong className="text-[1.6rem]">{fmtInr(s.saved, 2)}</strong>: {s.pct_saved ?? 0}% lower than starting when Slurm would have, across {s.jobs_measured} finished job
          {s.jobs_measured === 1 ? '' : 's'}.
        </p>
      ) : (
        <p className="text-[1.25rem] leading-snug" role="status">Nothing measured yet. A saving appears once a held job has really run.</p>
      )}
      <dl className="mt-5 grid grid-cols-2 gap-x-8 gap-y-4">
        {rows.map(([label, value]) => (
          <div key={label} className="border-t border-line pt-2">
            <dt className="text-[15px] text-ink-3">{label}</dt>
            <dd className="text-2xl font-medium">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-5 max-w-[46ch] text-[15px] text-ink-3">
        The start and end times are real. The electricity is <Term k="measured">modelled</Term> (GPUs times kW per GPU times the tariff), so the rupees are estimates. The plan is never added to the measured figure.
      </p>
    </div>
  )
}

function Log({ activity }: { activity: SiteView['activity'] }) {
  if (!activity.length) return <p className="text-ink-2">Nothing yet.</p>
  return (
    <ol className="max-h-72 overflow-auto">
      {activity.map((a, i) => (
        <li key={`${a.at}-${i}`} className="grid grid-cols-[84px_minmax(0,1fr)] gap-3 border-b border-line py-1.5 text-[15px]">
          <time dateTime={a.at} className="num text-ink-3">{fmtHMS(a.at)}</time>
          <span>{a.text}</span>
        </li>
      ))}
    </ol>
  )
}

export function Live() {
  const sites = useSites()
  const [picked, setPicked] = useState<string | null>(null)
  const siteId = picked ?? sites.data?.[0]?.id ?? null
  const poll = useSitePoll(siteId)

  if (sites.data && sites.data.length === 0) return <Recorded why="No cluster is connected right now." />
  if (!poll.data) {
    return poll.error || sites.error ? <Recorded why="The Wattshift cloud is not reachable right now." /> : (
      <>
        <Caption />
        <p role="status" className="py-8 text-ink-2">Loading…</p>
      </>
    )
  }
  const v = poll.data
  return (
    <>
      <Caption lapse={isTimelapse(v)} />
      {poll.error && (
        <Notice role="alert">
          The cloud is not reachable. Jobs already held are still held: Slurm enforces their start times by itself. Retrying every 2 seconds.
        </Notice>
      )}
      {sites.data && sites.data.length > 1 && (
        <label className="mb-3 flex items-center gap-3">
          <span className="font-semibold">Site</span>
          <select value={siteId ?? ''} onChange={(e) => setPicked(e.target.value)} className="field !w-auto">
            {sites.data.map((s) => (
              <option key={s.id} value={s.id}>{s.company} {s.name}</option>
            ))}
          </select>
        </label>
      )}

      <LiveControls view={v} refresh={poll.refresh} />

      <div className="grid gap-x-14 lg:grid-cols-[minmax(0,7fr)_minmax(0,4fr)]">
        <Section title="Jobs" note="One strip per job. A held job is pushed out of the rack: Slurm will not start it before its start time.">
          <ViewToggle label="the jobs" chart={<JobRack view={v} />} table={<JobTable jobs={v.jobs} />} />
        </Section>
        <Section title="Saved so far">
          <Tally view={v} />
        </Section>
      </div>

      <Section title="When each job starts, and why" note={timelineFinding(v)}>
        <ViewToggle label="the timeline" chart={<><Timeline view={v} timelapse={isTimelapse(v)} /><div className="mt-4"><TimelineKey /></div><p className="mt-2 text-[15px] text-ink-3">Time runs left to right, in hours:minutes:seconds.{isTimelapse(v) ? ' The tariff clock is sped up.' : ''}</p></>} table={<TimelineTable view={v} />} />
      </Section>

      <Section title="What just happened" note="Newest first.">
        <Log activity={v.activity} />
      </Section>
    </>
  )
}
