import { useId, useState } from 'react'
import { fmtInr } from '../lib/data'
import { fleetMonthly, fmtWatts, headline, replayNote, runWord } from '../lib/measured'
import { fmtDayHM } from '../lib/time'
import { ZONE_WORD } from '../lib/zones'
import type { Measured, MeasuredRun } from '../types'
import { DataTable, Field, Notice, Section, Term, ViewToggle } from './ui'

const Empty = ({ error }: { error: boolean }) => (
  <p className="max-w-[60ch] text-ink-2">{error ? 'Waiting for the Wattshift API to come up…' : 'Loading…'}</p>
)

/** What a run measured, in the table cell: watts, the band it started in, and its cost for one GPU-hour. */
const runCell = (r: MeasuredRun) =>
  r.rs_per_gpu_hour != null && r.zone ? `${runWord(r)}, ${ZONE_WORD[r.zone].toLowerCase()} hours, ${fmtInr(r.rs_per_gpu_hour, 2)}` : runWord(r)

/** The page's answer: two bars, the same job started at once and at Wattshift's hour, with a small fleet calculator. */
export function MeasuredSummary({ data, error }: { data: Measured | null; error: boolean }) {
  const uid = useId()
  const [gpus, setGpus] = useState('100')
  const [hours, setHours] = useState('8')
  const note = (
    <>
      Read from a real GPU, not modelled. Each job is run twice on a Kaggle GPU, once at once and once at the hour Wattshift chose, and the power is measured while it runs. Each run is priced at the Maharashtra
      tariff for the hour it started, per <Term k="gpuhour">GPU-hour</Term>.
    </>
  )
  if (!data) return <Section title="Measured on a real GPU" note={note}><Empty error={error} /></Section>

  const { summary: s, basis: b } = data
  const line = headline(s, b.gpu)
  if (!line || s.rs_per_gpu_hour_without == null || s.rs_per_gpu_hour_with == null || s.saved_rs_per_gpu_hour == null) {
    return (
      <Section title="Measured on a real GPU" note={note}>
        <p className="max-w-[62ch]" role="status">
          No finished pair yet.{s.waiting > 0 ? ` ${s.waiting} job${s.waiting === 1 ? ' is' : 's are'} still waiting for the second run.` : ''} A pair completes when a job has run both ways. Submit a job on the{' '}
          <a href="#/try">Try it</a> page between 5 pm and midnight, when the tariff is high, and its cheaper run follows when the cheap window opens.
        </p>
      </Section>
    )
  }
  const replay = replayNote(s)
  const max = Math.max(s.rs_per_gpu_hour_without, s.rs_per_gpu_hour_with) || 1
  const rows = [
    { label: 'Started at once, without Wattshift', v: s.rs_per_gpu_hour_without, w: s.avg_watts_without, cls: 'bg-ink-3' },
    { label: 'Started at the hour Wattshift chose', v: s.rs_per_gpu_hour_with, w: s.avg_watts_with, cls: 'bg-ink' },
  ]
  const g = Number(gpus)
  const h = Number(hours)
  const monthly = gpus.trim() !== '' && hours.trim() !== '' && Number.isFinite(g) && Number.isFinite(h) && g >= 0 && h >= 0 && h <= 24 ? fleetMonthly(s.saved_rs_per_gpu_hour, g, h) : null

  return (
    <Section title="Measured on a real GPU" note={note}>
      {replay && <Notice>{replay}</Notice>}
      <p className="max-w-[40ch] text-[clamp(1.5rem,3.2vw,2.3rem)] leading-tight [font-stretch:112%] [font-weight:700]" role="status">{line}</p>
      <p className="mt-2 text-[15px] text-ink-3">
        {s.pairs} finished pair{s.pairs === 1 ? '' : 's'} on a {b.gpu}, {b.tariff}. Energy charge only.
      </p>
      <div className="mt-8">
        <ViewToggle
          label="the two costs"
          chart={
            <ul className="max-w-2xl space-y-5">
              {rows.map((r) => (
                <li key={r.label} aria-label={`${r.label}: ${fmtInr(r.v, 2)} per GPU-hour, drawing ${fmtWatts(r.w!)}`}>
                  <div className="mb-1.5 flex flex-wrap justify-between gap-x-4 text-ink-2">
                    <span>{r.label}</span>
                    <span className="num">drew {fmtWatts(r.w!)}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="h-7 flex-1"><div className={`h-7 ${r.cls}`} style={{ width: `${(r.v / max) * 100}%` }} /></div>
                    <span className="num w-24 text-right text-[1.25rem] font-bold">{fmtInr(r.v, 2)}</span>
                  </div>
                </li>
              ))}
              <li className="text-[15px] text-ink-3">Cost of one GPU-hour, in rupees. The bars start at zero.</li>
            </ul>
          }
          table={<DataTable head={['Run', 'Average draw', 'Cost per GPU-hour']} numeric={[1, 2]} rows={rows.map((r) => [r.label, fmtWatts(r.w!), fmtInr(r.v, 2)])} />}
        />
      </div>

      <p className="mt-8 max-w-[62ch] text-ink-2">
        The two runs draw about the same power. The saving comes from when each one started, not from how it ran. A run lasts about 30 seconds, so the per-hour figures are the measured draw priced for a full hour.
      </p>

      <div className="mt-8 max-w-md border-t border-line pt-5">
        <h3 className="text-lg">What that adds up to for a fleet</h3>
        <div className="mt-4 grid grid-cols-2 gap-4">
          <Field id={`${uid}-g`} label="GPUs">
            <input id={`${uid}-g`} className="field num" type="number" inputMode="numeric" min={0} step={1} autoComplete="off" value={gpus} onChange={(e) => setGpus(e.target.value)} />
          </Field>
          <Field id={`${uid}-h`} label="Hours a day on jobs that can wait">
            <input id={`${uid}-h`} className="field num" type="number" inputMode="decimal" min={0} max={24} step="any" autoComplete="off" value={hours} onChange={(e) => setHours(e.target.value)} />
          </Field>
        </div>
        <p className="mt-4" role="status">
          {monthly === null ? (
            'Enter GPUs and hours between 0 and 24.'
          ) : (
            <>
              <strong className="num text-[1.4rem]">{fmtInr(monthly, 0)}</strong> a month for {g.toLocaleString('en-IN')} GPUs of this kind, over {h} hours a day for 30 days.
            </>
          )}
        </p>
        <p className="mt-2 text-[15px] text-ink-3">A {b.gpu} draws at most {b.power_limit_w} W. Bigger GPUs draw more, so their rupee saving is larger; the percentage stays about the same.</p>
      </div>
    </Section>
  )
}

/** Every job's two runs, side by side, with their state until both are done. */
export function MeasuredRuns({ data }: { data: Measured | null }) {
  if (!data || data.jobs.length === 0) return null
  return (
    <Section
      title="The two real runs of each job"
      note="Each job also runs once at once, as if Wattshift were not there. Both are measured on the Kaggle GPU, and each is priced at the tariff of the hour it started. Cost is per GPU-hour."
    >
      <DataTable
        head={['Job', 'Submitted', 'Without Wattshift', 'With Wattshift', 'Saved']}
        numeric={[4]}
        rows={data.jobs.map((j) => [
          <span translate="no">Job {j.job_id.slice(0, 4)}</span>,
          j.simulated ? `${fmtDayHM(j.submitted_at)}, replay clock` : fmtDayHM(j.submitted_at),
          runCell(j.without),
          runCell(j.with),
          j.saved_rs_per_gpu_hour != null ? `${fmtInr(j.saved_rs_per_gpu_hour, 2)}, ${j.pct_saved}%` : 'not yet',
        ])}
      />
    </Section>
  )
}
