import { useEffect, useId, useState } from 'react'
import { getHealth, submitJob, type Job } from '../api'
import { MeasuredRuns } from '../components/Measured'
import { DataTable, ErrorList, Field, Notice, PageHead, Section, SpeedNote, Term, ViewToggle } from '../components/ui'
import { fmtInr } from '../lib/data'
import { deadlineToIso, hoursAhead, MAX_DURATION_MIN, tomorrowAt, validateJob } from '../lib/job'
import { fmtDayHM } from '../lib/time'
import { ZONE_KEY, ZONE_WORD } from '../lib/zones'
import type { JobStatus, Snapshot } from '../types'
import { useMeasured } from '../useMeasured'
import type { PollState } from '../usePoll'

const KEY_STORE = 'wattshift.apiKey'
const readKey = () => {
  try {
    return sessionStorage.getItem(KEY_STORE) ?? ''
  } catch {
    return '' // storage can be blocked (private windows); the form still works
  }
}

type Outcome = { kind: 'placed'; job: Job } | { kind: 'queued'; job: Job; detail: string }

const FORM: Record<JobStatus, 'flat' | 'cocked' | 'notched' | 'struck'> = { queued: 'flat', scheduled: 'cocked', running: 'flat', done: 'notched', failed: 'struck' }
const WORD: Record<JobStatus, string> = { queued: 'Queued, no window yet', scheduled: 'Held for its window', running: 'Running', done: 'Done', failed: 'Failed' }

function JobStrip({ job }: { job: Job }) {
  const zone = job.tod_zone ? ZONE_KEY[job.tod_zone] : 'normal'
  return (
    <li className="strip" data-form={FORM[job.status]} data-zone={zone}>
      <div>
        <div className="name" translate="no">Job {job.id.slice(0, 4)}</div>
        <div className="cap">{job.duration_minutes} min</div>
      </div>
      <div className="min-w-0">
        <div className="strike font-semibold">
          {WORD[job.status]}
          {job.assigned_window_start && job.status !== 'done' ? ` until ${fmtDayHM(job.assigned_window_start)}` : ''}
        </div>
        <div className="cap mt-1">
          {job.tod_zone ? `${ZONE_WORD[job.tod_zone]} hours` : 'no window'}, deadline <span className="val">{fmtDayHM(job.deadline)}</span>
          {job.failure_reason ? `. ${job.failure_reason.slice(0, 60)}` : ''}
        </div>
      </div>
      <div className="val text-right max-sm:text-left">
        {job.baseline_cost_rs == null
          ? ''
          : job.actual_cost_rs != null
            ? `${fmtInr(job.baseline_cost_rs, 2)} became ${fmtInr(job.actual_cost_rs, 2)}`
            : job.planned_cost_rs != null
              ? `${fmtInr(job.baseline_cost_rs, 2)} planned as ${fmtInr(job.planned_cost_rs, 2)}`
              : `${fmtInr(job.baseline_cost_rs, 2)} if run now`}
      </div>
    </li>
  )
}

export function Try({ poll }: { poll: PollState }) {
  const uid = useId()
  const measured = useMeasured()
  const [canRun, setCanRun] = useState(true) // assume yes until the server says otherwise
  useEffect(() => {
    getHealth().then((h) => setCanRun(h.can_run_jobs), () => {})
  }, [])
  const d: Snapshot | null = poll.data
  const [duration, setDuration] = useState('60')
  const [deadline, setDeadline] = useState('')
  const [power, setPower] = useState('')
  const [apiKey, setApiKey] = useState(readKey)
  const [needsKey, setNeedsKey] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<Outcome | null>(null)

  if (!d) {
    return (
      <>
        <PageHead title="Try it">Give Wattshift a job and a deadline, and it picks the cheapest time to run it.</PageHead>
        <p role="status" className="py-8 text-ink-2">{poll.error ? 'Waiting for the Wattshift API to come up…' : 'Loading…'}</p>
      </>
    )
  }
  const nowIso = d.forecast.now
  if (!deadline) setDeadline(hoursAhead(nowIso, 12))

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    const draft = { durationMin: Number(duration), deadlineLocal: deadline, powerKw: power.trim() === '' ? null : Number(power), nowIso }
    const problems = validateJob(draft)
    setErrors(problems)
    setOutcome(null)
    if (problems.length) return
    setBusy(true)
    const r = await submitJob(
      { duration_minutes: draft.durationMin, deadline: deadlineToIso(deadline)!, provider: 'kaggle', ...(draft.powerKw !== null ? { power_kw: draft.powerKw } : {}) },
      apiKey || undefined,
    )
    setBusy(false)
    if (r.kind === 'placed' || r.kind === 'queued') {
      try {
        if (apiKey) sessionStorage.setItem(KEY_STORE, apiKey)
      } catch {
        /* storage blocked: fine */
      }
      setOutcome(r)
      poll.refresh()
    } else if (r.kind === 'auth') {
      setNeedsKey(true)
      setErrors([apiKey ? 'That API key was not accepted.' : 'This server needs an API key to accept jobs.'])
    } else if (r.kind === 'invalid') setErrors(r.messages)
    else setErrors([r.message])
  }

  const chip = (label: string, value: string) => (
    <button type="button" className="btn btn-small btn-quiet" onClick={() => setDeadline(value)}>{label}</button>
  )

  const jobs = [...d.jobs].sort((a, b) => b.submitted_at.localeCompare(a.submitted_at))
  const s = d.savings
  const finished = jobs.filter((j) => j.status === 'done' || j.status === 'failed')

  return (
    <>
      <PageHead title="Try it">
        Give Wattshift a job and a deadline. It picks the cheapest window that still finishes in time, holds the job until then, and runs it. In this demo the run is a short stand-in on a Kaggle GPU.
      </PageHead>
      <SpeedNote kind="real" title="Real speed.">
        Clocks and prices here are the real ones, so a held job can wait for hours before its cheap window opens. The GPU run itself is a short stand-in on a Kaggle GPU. The <a href="#/live">Live demo</a> is the sped-up version.
      </SpeedNote>
      {!canRun && (
        <Notice>
          This server cannot run jobs: it has no GPU to start them on. A job submitted here is planned and held, then marked failed when its time comes. The real runs on this site were made from the owner’s computer.
        </Notice>
      )}
      {poll.error && <Notice>Can’t reach the Wattshift API, so this is the last data received. Retrying every 5 seconds.</Notice>}

      <div className="grid gap-x-14 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <Section title="Submit a job">
          <form onSubmit={onSubmit} noValidate className="max-w-md space-y-5">
            <Field id={`${uid}-d`} label="Job length (minutes)" hint={`1 to ${MAX_DURATION_MIN}. It sets the cost and how much capacity the job takes.`}>
              <input id={`${uid}-d`} name="duration_minutes" className="field num" type="number" inputMode="numeric" min={1} max={MAX_DURATION_MIN} step={1} autoComplete="off" value={duration} onChange={(e) => setDuration(e.target.value)} />
            </Field>
            <Field id={`${uid}-dl`} label="Deadline (IST)" hint="The job must start early enough to finish by then.">
              <input id={`${uid}-dl`} name="deadline" className="field num" type="datetime-local" autoComplete="off" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
              <div className="mt-2 flex flex-wrap gap-2">
                {chip('In 6 hours', hoursAhead(nowIso, 6))}
                {chip('In 12 hours', hoursAhead(nowIso, 12))}
                {chip('Tomorrow 12:00', tomorrowAt(nowIso, 12))}
                {chip('Tomorrow 16:30', tomorrowAt(nowIso, 16, 30))}
              </div>
            </Field>
            <Field id={`${uid}-p`} label="Power draw (kW, optional)" hint="Modelled, not measured. Left blank it uses 10 kW, about one 8-GPU node.">
              <input id={`${uid}-p`} name="power_kw" className="field num" type="number" inputMode="decimal" min={0} max={1000} step="any" placeholder="10" autoComplete="off" value={power} onChange={(e) => setPower(e.target.value)} />
            </Field>
            {needsKey && (
              <Field id={`${uid}-k`} label="API key" hint="Kept in this browser tab only.">
                <input id={`${uid}-k`} name="api_key" className="field" type="password" autoComplete="off" spellCheck={false} value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
              </Field>
            )}
            <ErrorList errors={errors} />
            <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Submitting…' : 'Submit job'}</button>
          </form>

          {outcome && (
            <div role="status" className="mt-8 max-w-md border-t border-ink pt-4">
              {outcome.kind === 'placed' && outcome.job.assigned_window_start ? (
                <>
                  <h3 className="text-lg">Held for its window</h3>
                  <p className="mt-2">
                    It starts <strong className="num">{fmtDayHM(outcome.job.assigned_window_start)} IST</strong> in the {ZONE_WORD[outcome.job.tod_zone ?? 'baseline'].toLowerCase()} band.
                  </p>
                  <p className="mt-1 text-ink-2">
                    Planned cost {fmtInr(outcome.job.planned_cost_rs ?? 0, 2)}, against {fmtInr(outcome.job.baseline_cost_rs ?? 0, 2)} if it ran right now. It starts by itself at that time.
                  </p>
                </>
              ) : (
                <>
                  <h3 className="text-lg">Saved, but no window yet</h3>
                  <p className="mt-2 text-ink-2">
                    There is no window with spare capacity before the deadline yet. It stays queued and is placed as soon as prices covering the deadline arrive or capacity frees up. If neither happens before the deadline, it is marked failed.
                  </p>
                </>
              )}
            </div>
          )}
        </Section>

        <Section title="Your jobs" note="Newest first. A held job is pushed out of the rack until its window opens.">
          <ViewToggle
            label="the jobs"
            chart={jobs.length ? <ul className="rack">{jobs.map((j) => <JobStrip key={j.id} job={j} />)}</ul> : <p className="text-ink-2">No jobs yet. Submit one and it appears here with its window.</p>}
            table={
              <DataTable
                head={['Job', 'Status', 'Starts', 'Band', 'Length', 'Run at once', 'Planned', 'Deadline']}
                numeric={[4, 5, 6]}
                empty="No jobs yet."
                rows={jobs.map((j) => [
                  <span translate="no">Job {j.id.slice(0, 4)}</span>, WORD[j.status], j.assigned_window_start ? fmtDayHM(j.assigned_window_start) : 'none', j.tod_zone ? ZONE_WORD[j.tod_zone] : 'none',
                  `${j.duration_minutes} min`, j.baseline_cost_rs != null ? fmtInr(j.baseline_cost_rs, 2) : 'none', j.planned_cost_rs != null ? fmtInr(j.planned_cost_rs, 2) : 'none', fmtDayHM(j.deadline),
                ])}
              />
            }
          />
        </Section>
      </div>

      <MeasuredRuns data={measured.data} />

      <Section title="What it saved" note={<>Against running each job the moment it was submitted. The figures are <Term k="measured">modelled</Term>: the tariff times a 10 kW machine per job, not a measured bill.</>}>
        {s.jobs_counted === 0 ? (
          <p className="text-ink-2">No finished jobs yet. A saving appears once a held job has run.</p>
        ) : (
          <>
            <p className="max-w-[44ch] text-[1.25rem] leading-snug">
              <strong className="text-[1.6rem]">{fmtInr(s.total, 2)}</strong> saved, {s.pct_saved ?? 0}% lower than running immediately, across {s.jobs_counted} job{s.jobs_counted === 1 ? '' : 's'}.
            </p>
            <div className="mt-6 max-w-2xl">
              <DataTable
                head={['Day', 'Saved']}
                numeric={[1]}
                rows={s.daily.filter((x) => Math.abs(x.saved) > 0.005).map((x) => [x.date, fmtInr(x.saved, 2)])}
                empty="No day in the last week saved anything yet."
              />
              <p className="mt-2 text-[15px] text-ink-3">Days on which nothing finished are left out.</p>
            </div>
            {finished.length > 0 && (
              <div className="mt-8">
                <h3 className="mb-3 text-lg">Finished jobs</h3>
                <DataTable
                  head={['Job', 'Result', 'Started (IST)', 'Run at once', 'Actual', 'Saved']}
                  numeric={[3, 4, 5]}
                  rows={finished.map((j) => [
                    <span translate="no">Job {j.id.slice(0, 4)}</span>, j.status === 'done' ? 'Done' : `Failed${j.failure_reason ? `: ${j.failure_reason.slice(0, 48)}` : ''}`, j.executed_at ? fmtDayHM(j.executed_at) : 'none',
                    j.baseline_cost_rs != null ? fmtInr(j.baseline_cost_rs, 2) : 'none', j.actual_cost_rs != null ? fmtInr(j.actual_cost_rs, 2) : 'none', j.saved_rs != null ? fmtInr(j.saved_rs, 2) : 'none',
                  ])}
                />
              </div>
            )}
          </>
        )}
      </Section>
    </>
  )
}
