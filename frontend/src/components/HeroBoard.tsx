import { useEffect, useMemo, useState } from 'react'
import { useReducedMotion } from '../lib/useReducedMotion'
import { demoView, RECORDED_SUMMARY, STEPS } from '../lib/demo'
import { fmtInr } from '../lib/data'
import { LiveStrip, Timeline, TimelineKey } from './board'
import { Term } from './ui'

const LAST = STEPS.length - 1

/**
 * The recorded demo run, replayed. It draws the same strips and ruler as the Live demo page, from what the real
 * Slurm, agent and cloud produced, so what the visitor sees here is what the product shows.
 */
export function HeroBoard() {
  const reduce = useReducedMotion()
  const [step, setStep] = useState(reduce ? LAST : 0)
  const [playing, setPlaying] = useState(!reduce)

  useEffect(() => {
    if (!playing) return
    const t = setTimeout(() => setStep((s) => (s + 1) % STEPS.length), step === LAST ? 5200 : 3000)
    return () => clearTimeout(t)
  }, [step, playing])

  const view = useMemo(() => demoView(step), [step])
  const s = STEPS[step]

  return (
    <figure className="min-w-0" aria-label="A recorded run of the demo, replayed">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 pb-3 text-[15px]">
        <span className="font-semibold">A recorded run of the demo</span>
        <span className="text-ink-3">
          <Term k="timelapse">Time-lapsed</Term>: 2 minutes stand for 1 hour of the tariff
        </span>
      </div>

      <ul className="rack" aria-label="The three jobs">
        {view.jobs.map((j) => <LiveStrip key={j.ref} job={j} zones={view.zones} />)}
      </ul>

      <div className="mt-4">
        <Timeline view={view} minWidth={0} ticks={[0, 0.5, 1]} gutter={150} timelapse />
      </div>
      <div className="mt-3">
        <TimelineKey />
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-3 border-t border-ink pt-3">
        <p className="num text-[15px]">
          Wattshift: <strong>{s.wattshiftOff ? 'switched off' : s.mode === 'shadow' ? 'watching only' : 'setting start times'}</strong>
        </p>
        {step === LAST && (
          <p className="num text-[15px]" role="status">
            Measured saving <strong>{fmtInr(RECORDED_SUMMARY.saved, 2)}</strong> ({Math.round(RECORDED_SUMMARY.pct)}% lower)
          </p>
        )}
      </div>

      <figcaption className="mt-3">
        <p aria-live={playing ? 'off' : 'polite'} className="min-h-[5.2em] max-w-[52ch] text-[1.0625rem] leading-relaxed sm:min-h-[4em]">{s.caption}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button type="button" className="btn btn-small" onClick={() => setPlaying((p) => !p)}>
            {playing ? 'Pause' : 'Play'}
          </button>
          <div role="group" aria-label="Choose a moment" className="flex gap-1">
            {STEPS.map((st, i) => (
              <button
                key={i}
                type="button"
                aria-label={`Moment ${i + 1} of ${STEPS.length}: ${st.caption}`}
                aria-current={i === step ? 'step' : undefined}
                onClick={() => {
                  setPlaying(false)
                  setStep(i)
                }}
                className="btn btn-small btn-quiet !min-h-[36px] !w-[36px] !px-0 num aria-[current=step]:border-ink aria-[current=step]:bg-ink aria-[current=step]:text-board"
              >
                {i + 1}
              </button>
            ))}
          </div>
        </div>
      </figcaption>
    </figure>
  )
}
