import { useId } from 'react'
import { BAND_WORD, DAY, blockTop, clock12, hourWidth, hourX, rateScale, stackBottom } from '../lib/day'
import { bandAt } from '../lib/tariff'
import { fmtInr } from '../lib/data'
import type { Band } from '../lib/tariff'
import type { Zone } from '../types'

const WASH: Record<Zone, string> = {
  solar: 'var(--color-cheap-wash)',
  peak: 'var(--color-peak-wash)',
  baseline: 'color-mix(in srgb, var(--color-line) 40%, var(--color-board))',
}
const EDGE: Record<Zone, string> = { solar: 'var(--color-cheap)', peak: 'var(--color-peak)', baseline: 'var(--color-ink-3)' }

export interface DayMark {
  hour: number
  label: string
}

interface Props {
  bands: Band[]
  base: number
  /** the hour the three jobs start in; they sit on top of the price block under that hour */
  stackHour?: number
  stackLabel?: string
  /** where they would have started, drawn as dashed outlines */
  ghostHour?: number
  ghostLabel?: string
  nowHour?: number
  nowLabel?: string
  marks?: DayMark[]
  jobs?: string[]
  /** hide the empty space above the skyline when there is no stack to show */
  cropTop?: number
  label: string
}

/**
 * A day of electricity prices as a skyline: the taller the block, the more a unit costs. Each block says its price in
 * words and numbers right on it. Jobs are small tokens that sit on the block they start in.
 */
export function DayPicture({ bands, base, stackHour, stackLabel, ghostHour, ghostLabel, nowHour, nowLabel, marks = [], jobs = ['A', 'B', 'C'], cropTop = 0, label }: Props) {
  const arrowId = useId()
  const y = rateScale(bands, base)
  const T = DAY.TOKEN_H
  const G = DAY.GAP
  const w = DAY.TOKEN_W
  const cxOf = (h: number) => hourX(h) + hourWidth() / 2
  const stackH = jobs.length * (T + G)

  const at = (h: number) => ({ x: cxOf(h) - w / 2, cx: cxOf(h), bottom: stackBottom(bands, base, h) })
  const anchor = (h: number) => (cxOf(h) < 100 ? 'start' : cxOf(h) > DAY.W - 110 ? 'end' : 'middle')
  // A job label centred on a dot near midnight ran off the edge of the picture: keep the whole label inside it.
  const labelX = (h: number, label: string) => {
    const half = label.length * 5.5 + 6
    return Math.min(Math.max(cxOf(h), half), DAY.W - half)
  }
  const anchorX = (h: number) => (anchor(h) === 'start' ? 0 : anchor(h) === 'end' ? w : w / 2)

  // the hand-off arrow from where the jobs would have started to where they start
  let arrow: { d: string; mx: number; my: number } | null = null
  if (stackHour !== undefined && ghostHour !== undefined && ghostHour !== stackHour) {
    const g = at(ghostHour)
    const s = at(stackHour)
    const sx = g.cx
    const sy = g.bottom - stackH - 44
    const ex = s.x + w + 10
    const ey = s.bottom - stackH / 2
    const cx = (sx + ex) / 2
    const cy = Math.max(14, Math.min(sy, ey) - 46)
    arrow = { d: `M${sx} ${sy} Q${cx} ${cy} ${ex} ${ey}`, mx: 0.25 * sx + 0.5 * cx + 0.25 * ex, my: 0.25 * sy + 0.5 * cy + 0.25 * ey }
  }

  return (
    <svg viewBox={`0 ${cropTop} ${DAY.W} ${DAY.H - cropTop}`} role="img" aria-label={label} className="block h-auto w-full" style={{ fontFamily: 'var(--font-sans)' }}>
      <defs>
        <marker id={arrowId} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="8" markerHeight="8" orient="auto">
          <path d="M1 1.5 9 5 1 8.5" fill="none" stroke="var(--color-ink)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </marker>
      </defs>

      {bands.map((b, i) => {
        const top = y(base * (1 + b.pct / 100))
        const x0 = hourX(b.h0) + 1
        const x1 = hourX(b.h1) - 1
        return (
          <g key={i}>
            <rect x={x0} y={top} width={x1 - x0} height={DAY.BASE - top} fill={WASH[b.zone]} />
            <rect x={x0} y={top} width={x1 - x0} height={4} fill={EDGE[b.zone]} />
          </g>
        )
      })}

      {nowHour !== undefined && (
        <g>
          <line x1={hourX(nowHour)} x2={hourX(nowHour)} y1={cropTop + 30} y2={blockTop(bands, base, nowHour)} stroke="var(--color-ink)" strokeWidth="2" />
          <path d={`M${hourX(nowHour) - 8} ${DAY.BASE + 12} L${hourX(nowHour)} ${DAY.BASE + 1} L${hourX(nowHour) + 8} ${DAY.BASE + 12} Z`} fill="var(--color-ink)" />
        </g>
      )}

      {bands.map((b, i) => {
        const rate = base * (1 + b.pct / 100)
        const top = y(rate)
        const cx = (hourX(b.h0) + hourX(b.h1)) / 2
        return (
          <g key={i}>
            <text x={cx} y={top + 42} textAnchor="middle" fontSize="33" fontWeight="700" fill="var(--color-ink)" stroke={WASH[b.zone]} strokeWidth="7" paintOrder="stroke">{fmtInr(rate, 2)}</text>
            <text x={cx} y={top + 64} textAnchor="middle" fontSize="17" fill="var(--color-ink-2)" stroke={WASH[b.zone]} strokeWidth="6" paintOrder="stroke">per unit</text>
            <text x={cx} y={DAY.BASE - 40} textAnchor="middle" fontSize="22" fontWeight="700" fill="var(--color-ink)" stroke={WASH[b.zone]} strokeWidth="7" paintOrder="stroke">{BAND_WORD[b.zone]}</text>
            <text x={cx} y={DAY.BASE - 16} textAnchor="middle" fontSize="17" fill="var(--color-ink-2)" stroke={WASH[b.zone]} strokeWidth="6" paintOrder="stroke">{clock12(b.h0)} to {clock12(b.h1)}</text>
          </g>
        )
      })}

      <line x1={DAY.X0} x2={DAY.X1} y1={DAY.BASE} y2={DAY.BASE} stroke="var(--color-ink)" strokeWidth="1" />
      {[0, 6, 12, 18, 24].map((h) => (
        <g key={h}>
          <line x1={hourX(h)} x2={hourX(h)} y1={DAY.BASE} y2={DAY.BASE + 6} stroke="var(--color-ink)" />
          <text x={hourX(h)} y={DAY.BASE + 27} fontSize="17" fill="var(--color-ink-2)" textAnchor={h === 0 ? 'start' : h === 24 ? 'end' : 'middle'}>{clock12(h)}</text>
        </g>
      ))}

      {marks.map((m, i) => (
        <g key={`${m.label}-${i}`}>
          <circle cx={cxOf(m.hour)} cy={stackBottom(bands, base, m.hour) - 6} r="7" fill="var(--color-ink)" stroke="var(--color-board)" strokeWidth="2" />
          <text x={labelX(m.hour, m.label)} y={stackBottom(bands, base, m.hour) - 20 - (i % 2) * 16} fontSize="16" fontWeight="600" textAnchor="middle" fill="var(--color-ink)" stroke="var(--color-board)" strokeWidth="4" paintOrder="stroke">
            {m.label}
          </text>
        </g>
      ))}

      {nowHour !== undefined && (() => {
        const text = nowLabel ?? 'Now'
        const w2 = Math.max(70, text.length * 9.6 + 22)
        const cx = Math.min(Math.max(hourX(nowHour), w2 / 2 + 2), DAY.W - w2 / 2 - 2)
        return (
          <g>
            <rect x={cx - w2 / 2} y={cropTop + 4} width={w2} height={26} rx={3} fill="var(--color-ink)" />
            <text x={cx} y={cropTop + 22} textAnchor="middle" fontSize="17" fontWeight="700" fill="var(--color-board)">{text}</text>
          </g>
        )
      })()}

      {ghostHour !== undefined && ghostHour !== stackHour && (
        <g transform={`translate(${at(ghostHour).x} ${at(ghostHour).bottom})`}>
          {jobs.map((_, i) => (
            <rect key={i} x={0} y={-(i + 1) * (T + G) + G} width={w} height={T} rx={3} fill="none" stroke="var(--color-ink-3)" strokeWidth="1.5" strokeDasharray="3 3" />
          ))}
          {ghostLabel && (
            <text x={anchorX(ghostHour)} y={-stackH - 12} textAnchor={anchor(ghostHour)} fontSize="17" fill="var(--color-ink-2)" stroke="var(--color-board)" strokeWidth="4" paintOrder="stroke">{ghostLabel}</text>
          )}
        </g>
      )}

      {arrow && (
        <g>
          <path d={arrow.d} fill="none" stroke="var(--color-ink)" strokeWidth="2" markerEnd={`url(#${arrowId})`} />
          <text x={arrow.mx} y={arrow.my - 9} textAnchor="middle" fontSize="19" fontWeight="700" fill="var(--color-ink)" stroke="var(--color-board)" strokeWidth="5" paintOrder="stroke">
            Wattshift holds them
          </text>
        </g>
      )}

      {stackHour !== undefined && (
        <g style={{ transform: `translate(${at(stackHour).x}px, ${at(stackHour).bottom}px)`, transition: 'transform 1100ms cubic-bezier(0.16, 1, 0.3, 1)' }}>
          {jobs.map((name, i) => (
            <g key={name} transform={`translate(0 ${-(i + 1) * (T + G) + G})`}>
              <rect width={w} height={T} rx={3} fill="var(--color-strip)" stroke="var(--color-ink)" strokeWidth="1.5" />
              <rect x={5} y={(T - 8) / 2} width={8} height={8} rx={1.5} fill={EDGE[bandAt(bands, stackHour).zone]} />
              <text x={w / 2 + 6} y={T - 7.5} textAnchor="middle" fontSize="15" fontWeight="700" fill="var(--color-ink)">{name}</text>
            </g>
          ))}
          {stackLabel && (
            <text x={anchorX(stackHour)} y={-stackH - 12} textAnchor={anchor(stackHour)} fontSize="20" fontWeight="700" fill="var(--color-ink)" stroke="var(--color-board)" strokeWidth="5" paintOrder="stroke">{stackLabel}</text>
          )}
        </g>
      )}
    </svg>
  )
}
