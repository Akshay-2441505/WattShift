import { useEffect, useState } from 'react'
import { getBacktest, getBacktestSample, startBacktest } from '../api'
import { ErrorList, Field, PageHead, Section, SpeedNote, Swatch, Term } from '../components/ui'
import { insight, toRequest, validateAssumptions, type Draft } from '../lib/backtest'
import { fmtInr } from '../lib/data'
import { ZONE_KEY, ZONE_ORDER, ZONE_WORD } from '../lib/zones'
import type { BacktestResult, BacktestRun, BacktestSample } from '../types'

const KEY_STORE = 'wattshift.apiKey'
const readKey = () => {
  try {
    return sessionStorage.getItem(KEY_STORE) ?? ''
  } catch {
    return ''
  }
}
const TEMPLATE = 'job_id,submit_time,duration_minutes,gpus\njob-1,2026-08-17 19:30:00,45,8\njob-2,2026-08-18 02:10:00,12,1\n'
const pct = (x: number, d = 0) => `${(x * 100).toFixed(d)}%`

function Headline({ r }: { r: BacktestResult }) {
  const t = r.totals
  const i = insight(r.energy_by_length)
  return (
    <Section title="What timing would have saved" note={`${t.jobs.toLocaleString('en-IN')} jobs, ${r.tariff}`}>
      <p className="max-w-[40ch] text-[clamp(1.5rem,3.2vw,2.3rem)] leading-tight [font-stretch:112%] [font-weight:700]" role="status">
        <span className="num">{fmtInr(t.saved_rs)}</span>
        {t.pct_saved !== null ? `, ${t.pct_saved.toFixed(1)}% of a ${fmtInr(t.baseline_rs)} electricity bill.` : ', with no bill to compare against.'}
      </p>
      <p className="mt-2 text-[15px] text-ink-3">Energy charge only, modelled.</p>
      <p className="mt-5 max-w-[62ch]">
        {t.n_placed.toLocaleString('en-IN')} jobs were held for cheaper hours, waiting {t.avg_delay_hours.toFixed(1)} hours on average
        {t.n_unplaced > 0 ? `; ${t.n_unplaced.toLocaleString('en-IN')} had no cheaper slot with room and ran at once` : ''}.
      </p>
      <p className="mt-3 max-w-[62ch] text-ink-2">
        Timing can only move jobs of up to 3 hours, which hold <strong className="text-ink">{pct(i.movableEnergyShare)}</strong> of this file’s electricity. Jobs of {i.top.label.toLowerCase()} use {pct(i.top.energy_share)} of it, from just{' '}
        {pct(i.top.jobs_share)} of the jobs.
      </p>
    </Section>
  )
}

function EnergyByLength({ r }: { r: BacktestResult }) {
  const i = insight(r.energy_by_length)
  return (
    <Section
      title="Where the electricity goes"
      note={`Jobs of ${i.top.label.toLowerCase()} use ${pct(i.top.energy_share)} of the electricity, and they are only ${pct(i.top.jobs_share, 1)} of the jobs. The dark bar is that group.`}
    >
      <ul className="mb-4 flex gap-6 text-[15px] text-ink-2">
        <li className="flex items-center gap-2"><span aria-hidden className="inline-block h-2.5 w-6 bg-ink-3" />Share of the jobs</li>
        <li className="flex items-center gap-2"><span aria-hidden className="inline-block h-2.5 w-6 bg-ink" />Share of the electricity</li>
      </ul>
      <ul className="space-y-4">
        {r.energy_by_length.map((b) => (
          <li key={b.label} className="grid grid-cols-[104px_1fr] items-center gap-3" aria-label={`${b.label}: ${pct(b.jobs_share, 1)} of jobs, ${pct(b.energy_share, 1)} of electricity`}>
            <span className="text-ink-2">{b.label}</span>
            <div className="space-y-1">
              {[
                { v: b.jobs_share, cls: 'bg-ink-3' },
                { v: b.energy_share, cls: b.label === i.top.label ? 'bg-ink' : 'bg-ink-2' },
              ].map((bar, k) => (
                <div key={k} className="flex items-center gap-2">
                  <div className="h-2.5 flex-1">
                    <div className={`h-2.5 ${bar.cls}`} style={{ width: `${Math.max(bar.v * 100, 0.4)}%` }} />
                  </div>
                  <span className="num w-12 text-right text-[14px] text-ink-2">{pct(bar.v, bar.v < 0.1 ? 1 : 0)}</span>
                </div>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </Section>
  )
}

function ZoneShare({ r }: { r: BacktestResult }) {
  const rows = [
    { label: 'As submitted', d: r.kwh_by_zone_before },
    { label: 'With Wattshift', d: r.kwh_by_zone_after },
  ]
  const shareOf = (d: Record<string, number>, z: string) => (d[z] ?? 0) / (ZONE_ORDER.reduce((n, k) => n + (d[k] ?? 0), 0) || 1)
  return (
    <Section
      title="When the electricity is used"
      note={`As submitted, ${pct(shareOf(r.kwh_by_zone_before, 'peak'))} of the electricity ran in the expensive hours. With Wattshift, ${pct(shareOf(r.kwh_by_zone_after, 'peak'))} does.`}
    >
      <div className="space-y-6">
        {rows.map(({ label, d }) => {
          const tot = ZONE_ORDER.reduce((n, z) => n + (d[z] ?? 0), 0) || 1
          return (
            <div key={label}>
              <div className="mb-1.5 text-ink-2">{label}</div>
              <div className="flex h-9 overflow-hidden rounded-[3px] border border-line" role="img" aria-label={`${label}: ${ZONE_ORDER.map((z) => `${ZONE_WORD[z]} ${pct((d[z] ?? 0) / tot)}`).join(', ')}`}>
                {ZONE_ORDER.map((z) => {
                  const share = (d[z] ?? 0) / tot
                  return (
                    <div key={z} className={`band-${ZONE_KEY[z]} num flex items-center justify-center text-[14px] font-medium`} style={{ width: `${share * 100}%` }}>
                      {share >= 0.2 ? `${ZONE_WORD[z]} ${pct(share)}` : share >= 0.06 ? pct(share) : ''}
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
      <ul className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-[15px] text-ink-2">
        <li className="flex items-center gap-2"><Swatch kind="cheap" /> cheap hours</li>
        <li className="flex items-center gap-2"><Swatch kind="normal" /> normal hours</li>
        <li className="flex items-center gap-2"><Swatch kind="peak" /> expensive hours</li>
      </ul>
    </Section>
  )
}

function Grid({ r }: { r: BacktestResult }) {
  const g = r.sensitivity
  const slacks = g ? [...new Set(g.map((x) => x.slack_hours))].sort((a, b) => a - b) : []
  const shares = g ? [...new Set(g.map((x) => x.flexible_share))].sort((a, b) => a - b) : []
  const max = g ? Math.max(...g.map((x) => x.pct_saved ?? 0), 0.01) : 1
  const min = g ? Math.min(...g.map((x) => x.pct_saved ?? 0)) : 0
  const note = g
    ? `A job file can’t say which jobs are urgent, so this tries other guesses. Across all of them the saving runs from ${min.toFixed(1)}% to ${max.toFixed(1)}% of the bill. The outlined box is the guess you made.`
    : 'A job file can’t say which jobs are urgent, so this tries other guesses about which jobs can wait, and for how long.'
  return (
    <Section title="What if my guess is wrong?" note={note}>
      {!g ? (
        <p role="status" className="text-ink-2">Checking the other guesses. This can take a few minutes, and the main result above is already final.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="tbl num min-w-[420px]">
            <caption className="sr-only">Percent of the bill saved by share of jobs that can wait and hours they can wait</caption>
            <thead>
              <tr>
                <th scope="col" className="!font-sans">Jobs that can wait</th>
                {slacks.map((s) => (
                  <th key={s} scope="col" className="num !font-sans">wait up to {s} h</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shares.map((sh) => (
                <tr key={sh}>
                  <th scope="row" className="!border-line !font-sans !font-bold !text-ink">{pct(sh)}</th>
                  {slacks.map((sl) => {
                    const cell = g.find((x) => x.flexible_share === sh && x.slack_hours === sl)
                    const v = cell?.pct_saved ?? 0
                    const mine = sh === r.assumptions.flexible_share && sl === r.assumptions.slack_hours
                    return (
                      <td key={sl} className={`num ${mine ? 'outline outline-2 -outline-offset-2 outline-ink font-bold' : ''}`} style={{ background: `color-mix(in srgb, var(--color-ink) ${4 + (v / max) * 16}%, transparent)` }}>
                        {v.toFixed(1)}%
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  )
}

export function WhatIf() {
  const [sample, setSample] = useState<BacktestSample | null>(null)
  const [source, setSource] = useState<'sample' | 'upload'>('sample')
  const [fileText, setFileText] = useState<string | null>(null)
  const [fileName, setFileName] = useState('')
  const [retime, setRetime] = useState(false)
  const [draft, setDraft] = useState<Draft>({ flexiblePct: '50', slackHours: '12', gpus: '968', kwPerGpu: '1.25', capacityPct: '50' })
  const [errors, setErrors] = useState<string[]>([])
  const [apiKey] = useState(readKey)
  const [run, setRun] = useState<BacktestRun | null>(null)
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    getBacktestSample()
      .then((s) => {
        setSample(s)
        setDraft((d) => ({ ...d, gpus: String(s.fleet_gpus) }))
      })
      .catch(() => setErrors(['Could not reach the Wattshift API.']))
  }, [])

  useEffect(() => {
    if (!run || run.status === 'done' || run.status === 'error') return
    const t = setTimeout(async () => {
      try {
        setRun(await getBacktest(run.id))
      } catch {
        setRun({ ...run, status: 'error', error: 'Lost contact with the API while the report was running.' })
      }
    }, 1500)
    return () => clearTimeout(t)
  }, [run])

  const set = (k: keyof Draft) => (e: React.ChangeEvent<HTMLInputElement>) => setDraft({ ...draft, [k]: e.target.value })

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (!f) return
    setFileName(f.name)
    setFileText(await f.text())
  }

  async function onRun(e: React.FormEvent) {
    e.preventDefault()
    const problems = validateAssumptions(draft)
    if (source === 'upload' && !fileText) problems.unshift('Choose a jobs file to upload.')
    setErrors(problems)
    if (problems.length) return
    setStarting(true)
    setRun(null)
    const r = await startBacktest(toRequest(draft, source, fileText, retime), apiKey || undefined)
    setStarting(false)
    if (r.kind === 'started') setRun({ id: r.id, status: 'running', stage: 'queued', error: null, result: null })
    else if (r.kind === 'invalid') setErrors(r.messages)
    else if (r.kind === 'auth') setErrors(['This server needs an API key. Use the Try it page once to enter it, then run the report again.'])
    else if (r.kind === 'busy') setErrors(['Other reports are still running. Try again in a minute.'])
    else setErrors([r.message])
  }

  const busy = starting || run?.status === 'running' || run?.status === 'queued'
  const res = run?.result ?? null

  return (
    <>
      <PageHead title="What if">
        Replay a company’s past GPU jobs against real electricity prices, to see what timing them differently would have saved. Nothing is run. This is a <Term k="backtest">backtest</Term>.
      </PageHead>

      <SpeedNote kind="real" title="Real speed, past data.">
        A real job log from a 2020 GPU cluster is replayed against real electricity prices. It is a calculation on past data: nothing is run and nothing is sped up.
      </SpeedNote>

      <div className="grid gap-x-14 lg:grid-cols-[minmax(0,4fr)_minmax(0,7fr)]">
        <Section title="Your assumptions" note="The report bills each job twice: once as if it ran the moment it was submitted, and once with the jobs that can wait held for the cheapest hours.">
          <form onSubmit={onRun} noValidate className="space-y-5">
            <fieldset>
              <legend className="mb-2 font-semibold">Jobs</legend>
              <div className="space-y-2">
                <label className="flex items-start gap-3">
                  <input type="radio" name="source" checked={source === 'sample'} onChange={() => setSource('sample')} className="mt-1.5" />
                  <span>
                    A real GPU cluster’s jobs
                    {sample ? ` (${sample.jobs.toLocaleString('en-IN')} jobs, moved onto ${sample.price_window.from} to ${sample.price_window.to})` : ''}
                  </span>
                </label>
                <label className="flex items-start gap-3">
                  <input type="radio" name="source" checked={source === 'upload'} onChange={() => setSource('upload')} className="mt-1.5" />
                  <span>My own file</span>
                </label>
              </div>
            </fieldset>

            {source === 'sample' && sample && <p className="text-[15px] text-ink-3">{sample.source}</p>}

            {source === 'upload' && (
              <div className="space-y-4">
                <Field id="bt-file" label="Jobs file (CSV)" hint="Columns: job_id, submit_time, duration_minutes, gpus. Times without a zone are read as IST. Up to 20,000 jobs.">
                  <input id="bt-file" type="file" accept=".csv,text/csv" onChange={onFile} className="block w-full" />
                </Field>
                {fileName && <p className="text-[15px] text-ink-3">{fileName} loaded</p>}
                <a href={`data:text/csv;charset=utf-8,${encodeURIComponent(TEMPLATE)}`} download="wattshift-jobs-template.csv" className="font-semibold">Download a template</a>
                <label className="flex items-start gap-3">
                  <input type="checkbox" checked={retime} onChange={(e) => setRetime(e.target.checked)} className="mt-1.5" />
                  <span>My data is from another period: line its dates up with the price data{sample ? ` (${sample.price_window.from} to ${sample.price_window.to})` : ''}. Weekdays and times of day are kept.</span>
                </label>
              </div>
            )}

            <Field id="bt-flex" label="Jobs that can wait (%)" hint="A file can’t say which jobs are urgent, so this is your guess. The report also shows other guesses.">
              <input id="bt-flex" className="field num" type="number" inputMode="decimal" min={0} max={100} step="any" value={draft.flexiblePct} onChange={set('flexiblePct')} autoComplete="off" />
            </Field>
            <Field id="bt-slack" label="How long they can wait (hours)">
              <input id="bt-slack" className="field num" type="number" inputMode="decimal" min={0} max={72} step="any" value={draft.slackHours} onChange={set('slackHours')} autoComplete="off" />
            </Field>
            <Field id="bt-gpus" label="Fleet size (GPUs)" hint="Limits how much shifted work can share one 15-minute slot.">
              <input id="bt-gpus" className="field num" type="number" inputMode="numeric" min={1} step={1} value={draft.gpus} onChange={set('gpus')} autoComplete="off" />
            </Field>
            <details>
              <summary className="cursor-pointer font-semibold">More assumptions</summary>
              <div className="mt-4 space-y-5">
                <Field id="bt-kw" label="Power per GPU (kW)" hint="About one 8-GPU node at 10 kW, cooling included. Modelled, not measured.">
                  <input id="bt-kw" className="field num" type="number" inputMode="decimal" min={0} max={10} step="any" value={draft.kwPerGpu} onChange={set('kwPerGpu')} autoComplete="off" />
                </Field>
                <Field id="bt-cap" label="Fleet share that can run shifted work at once (%)">
                  <input id="bt-cap" className="field num" type="number" inputMode="decimal" min={0} max={100} step="any" value={draft.capacityPct} onChange={set('capacityPct')} autoComplete="off" />
                </Field>
              </div>
            </details>

            <ErrorList errors={errors} />
            <button type="submit" disabled={busy} className="btn btn-primary">{busy ? 'Working it out…' : 'Run report'}</button>
          </form>
        </Section>

        <div>
          {!run && (
            <Section title="What you will see">
              <ul className="max-w-[60ch] space-y-3">
                <li><strong>The saving</strong> in rupees and as a share of the electricity bill.</li>
                <li><strong>Where the electricity goes</strong>, by job length. This is usually the surprise: a few very long jobs use most of the power, and they can’t be moved.</li>
                <li><strong>When it is used</strong>, before and after, by band of the tariff.</li>
                <li><strong>Other guesses</strong> about which jobs can wait, so you can see how much the answer depends on them.</li>
              </ul>
            </Section>
          )}
          {run && run.status !== 'error' && !res && (
            <Section title="Working it out">
              <p role="status" className="text-ink-2">Replaying the jobs against the price data. The sample takes about half a minute.</p>
            </Section>
          )}
          {run?.status === 'error' && (
            <Section title="The report did not finish">
              <p role="alert" className="font-semibold">{run.error}</p>
            </Section>
          )}
          {res && (
            <>
              <Headline r={res} />
              <EnergyByLength r={res} />
              <ZoneShare r={res} />
              <Grid r={res} />
            </>
          )}
        </div>
      </div>
    </>
  )
}
