import { createContext, useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { GLOSSARY, termFor } from '../lib/glossary'

/* ---- the glossary: any dotted word opens it, the header button opens it, it scrolls to the word you asked about ---- */
const GlossaryContext = createContext<(key?: string) => void>(() => {})

export function GlossaryProvider({ children }: { children: ReactNode }) {
  const dlg = useRef<HTMLDialogElement>(null)
  const [focus, setFocus] = useState<string | null>(null)

  const open = (key?: string) => {
    setFocus(key ?? null)
    dlg.current?.show()
  }

  useEffect(() => {
    if (!focus) return
    document.getElementById(`gloss-${focus}`)?.scrollIntoView({ block: 'center' })
  }, [focus])

  return (
    <GlossaryContext.Provider value={open}>
      {children}
      <dialog
        ref={dlg}
        aria-labelledby="gloss-title"
        onKeyDown={(e) => e.key === 'Escape' && dlg.current?.close()}
        className="fixed inset-auto bottom-4 right-4 z-40 m-0 max-h-[72vh] w-[min(92vw,420px)] rounded-[3px] border border-ink bg-board p-0 shadow-[0_8px_24px_-8px_rgb(16_20_26/0.5)]"
      >
        <div className="flex items-baseline justify-between gap-4 border-b border-ink px-6 py-4">
          <h2 id="gloss-title" className="text-2xl">Words used here</h2>
          <button type="button" className="btn btn-small" onClick={() => dlg.current?.close()}>Close</button>
        </div>
        <dl className="max-h-[56vh] overflow-y-auto px-6 py-2">
          {GLOSSARY.map((g) => (
            <div key={g.key} id={`gloss-${g.key}`} className={`border-b border-line py-4 ${focus === g.key ? 'bg-recess -mx-6 px-6' : ''}`}>
              <dt className="font-bold">{g.term}</dt>
              <dd className="mt-1 max-w-[56ch] text-ink-2">{g.text}</dd>
            </div>
          ))}
        </dl>
      </dialog>
    </GlossaryContext.Provider>
  )
}

export const useGlossary = () => useContext(GlossaryContext)

/** A word with a meaning: dotted underline, opens the glossary at that word. */
export function Term({ k, children }: { k: string; children?: ReactNode }) {
  const open = useGlossary()
  const entry = termFor(k)
  return (
    <span
      role="button"
      tabIndex={0}
      className="term"
      onClick={() => open(k)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          open(k)
        }
      }}
      aria-label={`${children ?? entry?.term}: what does this mean?`}
    >
      {children ?? entry?.term}
    </span>
  )
}

/* ---- page frame ---------------------------------------------------------------------------------------------- */
/** Every screen opens with what it is, in one plain sentence. */
export function PageHead({ title, children }: { title: string; children: ReactNode }) {
  return (
    <header className="pb-8 pt-10 sm:pt-14">
      <h1 className="text-[clamp(2.1rem,5vw,3.4rem)]">{title}</h1>
      <p className="mt-4 max-w-[60ch] text-[1.1875rem] leading-relaxed text-ink-2">{children}</p>
    </header>
  )
}

export function Section({ title, note, children, className = '', id }: { title: string; note?: ReactNode; children: ReactNode; className?: string; id?: string }) {
  const hid = useId()
  return (
    <section id={id} aria-labelledby={hid} className={`border-t border-ink pb-12 pt-6 ${className}`}>
      <h2 id={hid} className="text-[clamp(1.5rem,3vw,2.1rem)]">{title}</h2>
      {note && <p className="mt-2 max-w-[62ch] text-ink-2">{note}</p>}
      <div className="mt-6">{children}</div>
    </section>
  )
}

/**
 * Every screen is either a time-lapse or at real speed, and says so. Shape carries it: the time-lapse note has a dashed
 * border and the real-speed note a solid one, so the difference never depends on colour.
 */
export function SpeedNote({ kind, title, children }: { kind: 'timelapse' | 'real'; title: string; children: ReactNode }) {
  return (
    <aside aria-label={title} className={`speed ${kind === 'timelapse' ? 'speed-lapse' : ''}`}>
      <strong>{title}</strong> {children}
    </aside>
  )
}

/** A message that has to be seen. Inverted so it cannot be mistaken for content. */
export function Notice({ children, role = 'status' }: { children: ReactNode; role?: 'status' | 'alert' }) {
  return (
    <p role={role} className="my-4 rounded-[3px] bg-ink px-4 py-3 text-[15px] font-medium text-board">
      {children}
    </p>
  )
}

/** Every chart and rack has a table version; this switches between them. */
export function ViewToggle({ chart, table, label }: { chart: ReactNode; table: ReactNode; label: string }) {
  const [showTable, setShowTable] = useState(false)
  return (
    <div>
      <div className="mb-3 flex justify-end">
        <button type="button" aria-pressed={showTable} className="btn btn-small btn-quiet" onClick={() => setShowTable((v) => !v)}>
          {showTable ? `Show ${label} as a picture` : `Show ${label} as a table`}
        </button>
      </div>
      {showTable ? table : chart}
    </div>
  )
}

export function DataTable({ head, rows, empty = 'Nothing to show yet.', numeric = [] }: { head: string[]; rows: ReactNode[][]; empty?: string; numeric?: number[] }) {
  if (!rows.length) return <p className="py-4 text-ink-2">{empty}</p>
  return (
    <div className="max-h-[70vh] overflow-auto">
      <table className="tbl">
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={h} scope="col" className={numeric.includes(i) ? 'num' : ''}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j} className={numeric.includes(j) ? 'num num' : ''}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block font-semibold">{label}</label>
      {children}
      {hint && <p className="mt-1.5 text-[15px] text-ink-3">{hint}</p>}
    </div>
  )
}

export function ErrorList({ errors }: { errors: string[] }) {
  if (!errors.length) return null
  return (
    <ul role="alert" className="space-y-1 rounded-[3px] bg-ink px-4 py-3 text-[15px] font-medium text-board">
      {errors.map((m) => <li key={m}>{m}</li>)}
    </ul>
  )
}

/** A short swatch for legends: the shape is the same one drawn in the picture. */
export function Swatch({ kind }: { kind: 'cheap' | 'peak' | 'normal' | 'hold' | 'run' | 'would' | 'start' }) {
  const base = 'inline-block align-middle'
  switch (kind) {
    case 'cheap': return <span aria-hidden className={`${base} h-3 w-6 border border-line band-cheap`} />
    case 'peak': return <span aria-hidden className={`${base} h-3 w-6 border border-line band-peak`} />
    case 'normal': return <span aria-hidden className={`${base} h-3 w-6 border border-line band-normal`} />
    case 'hold': return <span aria-hidden className={`${base} hatch h-3 w-6 text-ink-3`} />
    case 'run': return <span aria-hidden className={`${base} h-3 w-6 bg-ink`} />
    case 'would': return <span aria-hidden className={`${base} size-3 rounded-full border-2 border-ink`} />
    case 'start': return <span aria-hidden className={`${base} size-3 rounded-full bg-ink`} />
  }
}
