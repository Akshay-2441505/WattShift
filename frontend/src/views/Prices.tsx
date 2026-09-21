import { useMemo, useState } from 'react'
import type { Forecast, Job, Snapshot, Zone } from '../types'
import { DayPicture } from '../components/DayPicture'
import { DataTable, Notice, PageHead, Section, SpeedNote, Term, ViewToggle } from '../components/ui'
import { cheapestWindow, fmtInr } from '../lib/data'
import { BAND_WORD, PRICE_WORD, clock12 } from '../lib/day'
import { bandAt, dayBands, rateAt, type Band } from '../lib/tariff'
import { fmtDay, fmtDayHM, istFraction, istHour } from '../lib/time'

const STEP = 15 * 60000
const IST_OFFSET = 5.5 * 3600_000
const timeOf = (iso: string) => clock12(istFraction(iso))
const WASH: Record<Zone, string> = { solar: 'var(--color-cheap-wash)', peak: 'var(--color-peak-wash)', baseline: 'color-mix(in srgb, var(--color-line) 40%, transparent)' }
const EDGE: Record<Zone, string> = { solar: 'var(--color-cheap)', peak: 'var(--color-peak)', baseline: 'var(--color-ink-3)' }

const nextChange = (bands: Band[], nowHour: number): { at: number; band: Band } => {
  const cur = bandAt(bands, nowHour)
  for (let h = Math.floor(nowHour) + 1; h <= Math.floor(nowHour) + 25; h++) {
    const b = bandAt(bands, h % 24)
    if (b.zone !== cur.zone || b.pct !== cur.pct) return { at: h % 24, band: b }
  }
  return { at: (Math.floor(nowHour) + 1) % 24, band: cur }
}

/* ---- price forecast: one line, one finding, direct labels ---------------------------------------------------- */
interface Row { t: number; price: number; zone: Zone; billed: number }

function PriceChart({ forecast }: { forecast: Forecast }) {
  const rows: Row[] = useMemo(() => forecast.blocks.map((b) => ({ t: new Date(b.ts).getTime(), price: b.effective_rate / 1000, zone: b.tod_zone, billed: b.billed_rs_kwh })), [forecast])
  const [pick, setPick] = useState<number | null>(null)
  if (!rows.length) return null

  const W = 640
  const H = 300
  const L = 54
  const R = 12
  const TOP = 44
  const BOT = 36
  const t0 = rows[0].t
  const t1 = rows[rows.length - 1].t + STEP
  const x = (t: number) => L + ((t - t0) / (t1 - t0)) * (W - L - R)
  const hi = Math.max(...rows.map((r) => r.price))
  const top = Math.ceil(hi * 1.15)
  const y = (p: number) => TOP + (1 - p / top) * (H - TOP - BOT)
  const nowT = new Date(forecast.now).getTime()
  const best = cheapestWindow(forecast.blocks, 60)

  const runs: { zone: Zone; a: number; b: number }[] = []
  for (const r of rows) {
    const last = runs[runs.length - 1]
    if (last && last.zone === r.zone && r.t - last.b <= STEP) last.b = r.t + STEP
    else runs.push({ zone: r.zone, a: r.t, b: r.t + STEP })
  }
  const path = rows.map((r, i) => `${i === 0 ? 'M' : 'L'}${x(r.t).toFixed(1)} ${y(r.price).toFixed(1)} L${x(r.t + STEP).toFixed(1)} ${y(r.price).toFixed(1)}`).join(' ')

  const span = t1 - t0
  const stepMs = span <= 6 * 3600_000 ? 3600_000 : 3 * 3600_000
  const ticks: number[] = []
  for (let t = Math.ceil((t0 + IST_OFFSET) / stepMs) * stepMs - IST_OFFSET; t <= t1; t += stepMs) ticks.push(t)
  const yTicks = [0, Math.round(top / 2), top]

  const move = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    const vx = ((e.clientX - box.left) / box.width) * W
    const t = t0 + ((vx - L) / (W - L - R)) * (t1 - t0)
    const i = rows.findIndex((r) => t >= r.t && t < r.t + STEP)
    setPick(i >= 0 ? i : null)
  }
  const key = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowRight') setPick((p) => Math.min((p ?? -1) + 1, rows.length - 1))
    else if (e.key === 'ArrowLeft') setPick((p) => Math.max((p ?? rows.length) - 1, 0))
    else if (e.key === 'Escape') setPick(null)
    else return
    e.preventDefault()
  }
  const at = pick !== null ? rows[pick] : null
  const bestX0 = best ? x(new Date(best.start).getTime()) : 0
  const bestX1 = best ? x(new Date(best.end).getTime()) : 0

  return (
    <div>
      <p className="mb-1 text-[15px] text-ink-2">Price per unit on the exchange, with the tariff band applied (₹)</p>
      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="block h-auto w-full min-w-[560px] touch-none outline-offset-4"
          style={{ fontFamily: 'var(--font-sans)' }}
          role="img"
          tabIndex={0}
          aria-label="The exchange price for each quarter hour ahead, as a line over the cheap, normal and expensive bands. Use the left and right arrow keys to read each value, or open the table."
          onPointerMove={move}
          onPointerLeave={() => setPick(null)}
          onKeyDown={key}
        >
          {runs.map((r, i) => {
            const w = x(r.b) - x(r.a)
            return (
              <g key={i}>
                <rect x={x(r.a)} y={TOP - 20} width={Math.max(0, w)} height={H - TOP - BOT + 20} fill={WASH[r.zone]} />
                <rect x={x(r.a)} y={TOP - 20} width={Math.max(0, w)} height={3} fill={EDGE[r.zone]} />
                {w > 130 && (
                  <text x={x(r.a) + 8} y={TOP - 2} fontSize="16" fontWeight="700" fill="var(--color-ink)">
                    {BAND_WORD[r.zone]}: {PRICE_WORD[r.zone]} hours
                  </text>
                )}
              </g>
            )
          })}
          {yTicks.map((v) => (
            <g key={v}>
              <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="var(--color-line)" />
              <text x={L - 8} y={y(v) + 5} fontSize="16" textAnchor="end" fill="var(--color-ink-2)" style={{ fontVariantNumeric: 'tabular-nums' }}>{`₹${v}`}</text>
            </g>
          ))}
          {best && <rect x={bestX0} y={TOP - 20} width={bestX1 - bestX0} height={H - TOP - BOT + 20} fill="none" stroke="var(--color-ink)" strokeWidth="2.5" />}
          <path d={path} fill="none" stroke="var(--color-ink)" strokeWidth="3" />
          {nowT >= t0 && nowT <= t1 && <line x1={x(nowT)} x2={x(nowT)} y1={TOP + 4} y2={H - BOT} stroke="var(--color-ink)" strokeWidth="2" />}
          {nowT >= t0 && nowT <= t1 && (
            <text x={x(nowT)} y={H - BOT + 30} fontSize="16" fontWeight="700" textAnchor="middle" fill="var(--color-ink)">now</text>
          )}
          {ticks.map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={H - BOT} y2={H - BOT + 6} stroke="var(--color-ink)" />
              <text x={x(t)} y={H - BOT + 24} fontSize="16" textAnchor={x(t) > W - 70 ? 'end' : x(t) < L + 24 ? 'start' : 'middle'} fill="var(--color-ink-2)" style={{ fontVariantNumeric: 'tabular-nums' }} opacity={nowT >= t0 && Math.abs(x(t) - x(nowT)) < 46 ? 0 : 1}>
                {istHour(new Date(t).toISOString()) === 0 ? `${fmtDay(new Date(t).toISOString())}, 12 am` : clock12(istFraction(new Date(t).toISOString()))}
              </text>
            </g>
          ))}
          {at && (
            <g>
              <line x1={x(at.t + STEP / 2)} x2={x(at.t + STEP / 2)} y1={TOP + 4} y2={H - BOT} stroke="var(--color-ink)" strokeWidth="1.5" />
              <circle cx={x(at.t + STEP / 2)} cy={y(at.price)} r="6" fill="var(--color-ink)" stroke="var(--color-board)" strokeWidth="2" />
            </g>
          )}
        </svg>
      </div>
      <p className="mt-3 min-h-[3.2em] text-[1.125rem] leading-snug" aria-live="polite">
        {at ? (
          <>
            <strong>{timeOf(new Date(at.t).toISOString())}</strong>: {fmtInr(at.price, 2)} per unit, {PRICE_WORD[at.zone]} hours.
          </>
        ) : best ? (
          <>
            The boxed hour is the cheapest coming up: <strong>{timeOf(best.start)} to {timeOf(best.end)}</strong>, about {fmtInr(best.avg / 1000, 2)} per unit. Hover or use the arrow keys to read any moment.
          </>
        ) : (
          'Hover or use the arrow keys to read any moment.'
        )}
      </p>
    </div>
  )
}

export function Prices({ data, error }: { data: Snapshot | null; error: string | null }) {
  if (!data) {
    return (
      <>
        <PageHead title="Prices today">What electricity costs across the day in Maharashtra, and which hours are cheap.</PageHead>
        <p role="status" className="py-8 text-ink-2">{error ? 'Waiting for the Wattshift API to come up…' : 'Loading…'}</p>
      </>
    )
  }
  const { forecast, tariff, jobs } = data
  const bands = dayBands(tariff.rules, tariff.season_now)
  const nowHour = istFraction(forecast.now)
  const cur = bandAt(bands, nowHour)
  const next = nextChange(bands, nowHour)
  const rate = rateAt(bands, tariff.base_rate_rs_kwh, nowHour)
  const placed = jobs.filter((j: Job) => j.assigned_window_start && j.status === 'scheduled').slice(0, 6)

  const bandRows = bands.map((b) => [
    `${BAND_WORD[b.zone]} (${PRICE_WORD[b.zone]})`,
    `${clock12(b.h0)} to ${clock12(b.h1)}`,
    fmtInr(tariff.base_rate_rs_kwh * (1 + b.pct / 100), 2),
    b.pct === 0 ? 'the base rate' : `${Math.abs(b.pct)}% ${b.pct > 0 ? 'more' : 'less'}`,
  ])

  return (
    <>
      <PageHead title="Prices today">
        What electricity costs across the day in Maharashtra, and which hours are cheap. Wattshift uses this to decide when a <Term k="flexible">flexible job</Term> should start.
      </PageHead>
      {forecast.mode === 'replay' ? (
        <SpeedNote kind="timelapse" title="Replay clock.">
          The clock on this page runs {forecast.sim_scale} times faster than real time, over a real past day of exchange prices. The time and the prices here are not today’s.
        </SpeedNote>
      ) : (
        <SpeedNote kind="real" title="Real speed.">
          This is the real clock, the real tariff and the exchange’s own prices. A job held for a cheap window here may wait for hours. The <a href="#/live">Live demo</a> is the sped-up version.
        </SpeedNote>
      )}
      {error && <Notice>Can’t reach the Wattshift API, so this is the last data received. Retrying every 5 seconds.</Notice>}

      <section className="border-t border-ink pb-10 pt-6" aria-label="Right now">
        <p className="max-w-[26ch] text-[clamp(1.7rem,3.8vw,2.7rem)] leading-tight [font-stretch:112%] [font-weight:700]">
          It is {timeOf(forecast.now)}. Electricity is {PRICE_WORD[cur.zone]} right now: {fmtInr(rate, 2)} a unit.
        </p>
        <p className="mt-3 max-w-[60ch] text-ink-2">It changes to {PRICE_WORD[next.band.zone]} hours at {clock12(next.at)}.</p>
      </section>

      <Section
        title="Today's prices in one picture"
        note="The taller the block, the more one unit of electricity costs. A unit is one kilowatt-hour. Jobs you queue on the Try it page appear as dots where they will start."
      >
        <ViewToggle
          label="the prices"
          chart={
            <div className="max-w-[880px]">
              <DayPicture
                bands={bands}
                base={tariff.base_rate_rs_kwh}
                nowHour={nowHour}
                nowLabel={`Now ${timeOf(forecast.now)}`}
                marks={placed.map((j) => ({ hour: istFraction(j.assigned_window_start!), label: `Job ${j.id.slice(0, 4)}` }))}
                jobs={[]}
                cropTop={100}
                label={`A day of electricity prices. ${bandRows.map((r) => `${r[0]}, ${r[1]}: ${r[2]} per unit`).join('. ')}. It is ${timeOf(forecast.now)} now.`}
              />
            </div>
          }
          table={
            <div className="space-y-8">
              <DataTable head={['Band', 'Hours', 'Price per unit', 'Against the base rate']} numeric={[2]} rows={bandRows} />
              <DataTable
                head={['Queued job', 'Starts', 'Length', 'Band']}
                numeric={[2]}
                empty="No jobs are queued. Give one a deadline on the Try it page and it will appear here."
                rows={placed.map((j) => [<span translate="no">{j.id.slice(0, 8)}</span>, fmtDayHM(j.assigned_window_start!), `${j.duration_minutes} min`, j.tod_zone ? PRICE_WORD[j.tod_zone] : 'not set'])}
              />
            </div>
          }
        />
        <p className="mt-3 max-w-[64ch] text-[15px] text-ink-3">
          Base rate {fmtInr(tariff.base_rate_rs_kwh, 2)} per unit, high-tension industrial. {tariff.verified ? 'Checked against the MERC order.' : 'This tariff has passed its end date, so the rupee figures are illustrative.'}
        </p>
      </Section>

      <Section title="The exchange price, hour by hour" note={<>The forecast comes from the Indian Energy Exchange (<Term k="iex">IEX</Term>). This is a different number from the tariff price above: it also follows the market price, which moves every 15 minutes. Wattshift starts a job in the cheapest hour that still meets its deadline.</>}>
        {forecast.blocks.length === 0 ? (
          <p className="text-ink-2">No price data for the coming hours yet. The exchange publishes tomorrow’s prices around midday.</p>
        ) : (
          forecast.blocks.length < 24 ? (
            <>
              <p className="mb-4 max-w-[62ch]">
                Only {(forecast.blocks.length / 4).toFixed(1)} hours of forecast are available so far, which is too little to draw a useful line. The exchange publishes tomorrow’s prices around midday, and the chart appears once there are at least six hours.
              </p>
              <DataTable
                head={['Time (IST)', 'Band', 'Price per unit (exchange, tariff applied)']}
                numeric={[2]}
                rows={forecast.blocks.map((b) => [fmtDayHM(b.ts), PRICE_WORD[b.tod_zone], fmtInr(b.effective_rate / 1000, 2)])}
              />
            </>
          ) : (
          <ViewToggle
            label="the forecast"
            chart={<PriceChart forecast={forecast} />}
            table={
              <DataTable
                head={['Time (IST)', 'Band', 'Exchange ₹ per MWh', 'Price per unit', 'Billed per unit', 'Carbon (modelled)']}
                numeric={[2, 3, 4, 5]}
                rows={forecast.blocks.map((b) => [fmtDayHM(b.ts), PRICE_WORD[b.tod_zone], b.iex_price.toFixed(0), fmtInr(b.effective_rate / 1000, 2), fmtInr(b.billed_rs_kwh, 2), b.carbon_index])}
              />
            }
          />
          )
        )}
      </Section>
    </>
  )
}
