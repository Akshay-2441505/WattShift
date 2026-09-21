import { useEffect, useId, useMemo, useState } from 'react'
import { fmtInr } from '../lib/data'
import { PRICE_WORD, BAND_WORD, clock12 } from '../lib/day'
import { bandAt, cheapestStart, jobCost, rateAt } from '../lib/tariff'
import { useReducedMotion } from '../lib/useReducedMotion'
import { useTariffRules } from '../lib/useTariffRules'
import { DayPicture } from './DayPicture'
import { DataTable, Term, ViewToggle } from './ui'

const KW = 10 // one 8-GPU node, modelled
const NOW = 20 // "start now" is 8 pm, in the expensive evening

/** The front page's picture: the same three jobs started now, or held until the cheap hours. */
export function HeroDay() {
  const { bands, base } = useTariffRules()
  const reduce = useReducedMotion()
  const [moved, setMoved] = useState(false)

  useEffect(() => {
    if (reduce) {
      setMoved(true)
      return
    }
    const t = setTimeout(() => setMoved(true), 2200) // show "start now" first, then let them slide once
    return () => clearTimeout(t)
  }, [reduce])

  const m = useMemo(() => {
    const cheap = cheapestStart(bands, base, 1, KW)
    const now = jobCost(bands, base, NOW, 1, KW)
    const held = jobCost(bands, base, cheap, 1, KW)
    return { cheap, now, held, pct: Math.round((1 - held / now) * 100) }
  }, [bands, base])

  return (
    <figure className="min-w-0">
      <DayPicture
        bands={bands}
        base={base}
        jobs={['A', 'B', 'C']}
        stackHour={moved ? m.cheap : NOW}
        stackLabel={moved ? `Held until ${clock12(m.cheap)}` : `Start now, ${clock12(NOW)}`}
        ghostHour={moved ? NOW : undefined}
        ghostLabel={moved ? clock12(NOW) : undefined}
        label={`A day of electricity prices. Night costs ${fmtInr(rateAt(bands, base, 3), 2)} per unit, daytime ${fmtInr(rateAt(bands, base, 12), 2)}, evening ${fmtInr(rateAt(bands, base, 20), 2)}. Three jobs ${moved ? `held until ${clock12(m.cheap)}` : `starting at ${clock12(NOW)}`}.`}
      />
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <div role="group" aria-label="When the jobs start" className="seg">
          <button type="button" aria-pressed={!moved} onClick={() => setMoved(false)}>Start now, {clock12(NOW)}</button>
          <button type="button" aria-pressed={moved} onClick={() => setMoved(true)}>Wait for the cheap hours</button>
        </div>
      </div>
      <figcaption className="mt-4" aria-live="polite">
        <p className="text-[1.375rem] font-bold leading-snug">
          {moved ? (
            <>
              Held until {clock12(m.cheap)}, each job costs {fmtInr(m.held, 2)} instead of {fmtInr(m.now, 2)}. Same work, {m.pct}% cheaper.
            </>
          ) : (
            <>Started at {clock12(NOW)}, each job costs {fmtInr(m.now, 2)}. That is the expensive part of the day.</>
          )}
        </p>
        <p className="mt-2 max-w-[54ch] text-[1.0625rem] leading-snug text-ink-2">
          <strong>Best case, not a promise.</strong> This sets the dearest hour against the cheapest, so real workloads usually save less. A unit is one kilowatt-hour, and each job is a one-hour run on a 10 kW
          machine at the real Maharashtra tariff. The rupee figures are <Term k="measured">modelled</Term>.
        </p>
      </figcaption>
    </figure>
  )
}

/** Drag the start time of a job along the day and watch its price change. */
export function DragDay() {
  const id = useId()
  const { bands, base } = useTariffRules()
  const [hour, setHour] = useState(20)

  const m = useMemo(() => {
    const best = cheapestStart(bands, base, 1, KW)
    const here = jobCost(bands, base, hour, 1, KW)
    const cheap = jobCost(bands, base, best, 1, KW)
    return { best, here, cheap, saved: here - cheap, pct: here > 0 ? Math.round(((here - cheap) / here) * 100) : 0 }
  }, [bands, base, hour])

  const zone = bandAt(bands, hour).zone
  const picture = (
    <>
      <DayPicture
        bands={bands}
        base={base}
        jobs={['A']}
        cropTop={90}
        stackHour={hour}
        stackLabel={clock12(hour)}
        label={`A day of electricity prices with one job starting at ${clock12(hour)}, in the ${PRICE_WORD[zone]} band.`}
      />
      <label htmlFor={id} className="mt-3 block font-semibold">
        When does the job start? <span className="ml-2 text-xl font-bold">{clock12(hour)}</span>
      </label>
      <input id={id} type="range" min={0} max={23.75} step={0.25} value={hour} onChange={(e) => setHour(Number(e.target.value))} className="mt-2 h-8 w-full" />
    </>
  )

  const hourly = Array.from({ length: 24 }, (_, h) => h)
  const table = (
    <DataTable
      head={['Start time', 'Band', 'Price per unit', 'Cost of the job']}
      numeric={[2, 3]}
      rows={hourly.map((h) => [clock12(h), `${BAND_WORD[bandAt(bands, h).zone]}, ${PRICE_WORD[bandAt(bands, h).zone]}`, fmtInr(rateAt(bands, base, h), 2), fmtInr(jobCost(bands, base, h, 1, KW), 2)])}
    />
  )

  return (
    <div className="grid items-start gap-x-14 gap-y-8 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
      <ViewToggle label="the prices" chart={picture} table={table} />
      <div aria-live="polite" className="max-w-[38ch] space-y-3 text-[1.25rem] leading-snug">
        <p>
          Starting at <strong>{clock12(hour)}</strong>, in the {PRICE_WORD[zone]} hours, costs <strong className="text-[1.6rem]">{fmtInr(m.here, 2)}</strong>.
        </p>
        <p>
          Starting at <strong>{clock12(m.best)}</strong>, the cheapest time, costs <strong className="text-[1.6rem]">{fmtInr(m.cheap, 2)}</strong>.
        </p>
        <p>
          {m.saved > 0.005 ? (
            <>Waiting saves <strong>{fmtInr(m.saved, 2)}</strong>, which is {m.pct}% less for the same work.</>
          ) : (
            <>This is already the cheapest time, so there is nothing to gain by waiting.</>
          )}
        </p>
      </div>
    </div>
  )
}
