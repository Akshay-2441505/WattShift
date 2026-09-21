import { useEffect, useState } from 'react'
import { usePoll } from './usePoll'
import { useTheme } from './lib/useTheme'
import { GlossaryProvider, Notice, useGlossary } from './components/ui'
import { fmtDayHM, fmtHM } from './lib/time'
import { Home } from './views/Home'
import { Live } from './views/Live'
import { Measured } from './views/Measured'
import { Prices } from './views/Prices'
import { Try } from './views/Try'
import { WhatIf } from './views/WhatIf'

const TABS = [
  { id: 'home', label: 'Start here', hash: '#/', title: 'Wattshift' },
  { id: 'live', label: 'Live demo', hash: '#/live', title: 'Live demo' },
  { id: 'prices', label: 'Prices today', hash: '#/prices', title: 'Prices today' },
  { id: 'whatif', label: 'What if', hash: '#/whatif', title: 'What if' },
  { id: 'measured', label: 'Measured', hash: '#/measured', title: 'Measured' },
  { id: 'try', label: 'Try it', hash: '#/try', title: 'Try it' },
] as const
type Tab = (typeof TABS)[number]['id']

// The header separates the sped-up demo from everything that runs at real speed.
const GROUPS: { id: string; label: string | null; tabs: Tab[] }[] = [
  { id: 'start', label: null, tabs: ['home'] },
  { id: 'lapse', label: 'Time-lapse demo', tabs: ['live'] },
  { id: 'real', label: 'Real speed', tabs: ['prices', 'whatif', 'measured', 'try'] },
]

// Earlier versions had six tabs; old links still land somewhere sensible.
const LEGACY: Record<string, Tab> = { dashboard: 'prices', forecast: 'prices', jobs: 'try', history: 'try', backtest: 'whatif' }

const fromHash = (): Tab => {
  const id = location.hash.replace(/^#\/?/, '')
  return TABS.find((t) => t.id === id)?.id ?? LEGACY[id] ?? 'home'
}

/** Two strips on a rail, one pushed out: a held job. */
function Mark() {
  return (
    <svg width="30" height="26" viewBox="0 0 30 26" aria-hidden>
      <rect x="0" y="2" width="22" height="9" rx="1" fill="currentColor" />
      <rect x="6" y="15" width="22" height="9" rx="1" fill="currentColor" />
      <rect x="6" y="15" width="5" height="9" fill="var(--color-cheap)" />
    </svg>
  )
}

function Shell() {
  const [tab, setTab] = useState<Tab>(fromHash)
  const poll = usePoll(5000)
  const openWords = useGlossary()
  const theme = useTheme()

  useEffect(() => {
    const on = () => {
      setTab(fromHash())
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  useEffect(() => {
    const t = TABS.find((x) => x.id === tab)!
    document.title = tab === 'home' ? 'Wattshift: run flexible jobs when electricity is cheap' : `${t.title} - Wattshift`
  }, [tab])

  const d = poll.data
  return (
    <div className="mx-auto max-w-[1240px] px-5 sm:px-8">
      <a href="#main" className="skip-link" onClick={(e) => { e.preventDefault(); document.getElementById('main')?.focus() }}>
        Skip to content
      </a>
      <header className="flex flex-wrap items-center gap-x-8 gap-y-2 py-4">
        <a href="#/" className="flex items-center gap-2.5 no-underline" aria-label="Wattshift, start here">
          <Mark />
          <span className="text-[1.5rem] leading-none [font-stretch:112%] [font-weight:800] tracking-tight">Wattshift</span>
        </a>
        <nav aria-label="Areas" className="order-3 -mx-1 w-full overflow-x-auto [scrollbar-width:none] sm:order-none sm:mx-0 sm:w-auto sm:flex-1 [&::-webkit-scrollbar]:hidden">
          <ul className="flex items-end gap-x-4 whitespace-nowrap">
            {GROUPS.map((g) => (
              <li key={g.id} role={g.label ? 'group' : undefined} aria-label={g.label ?? undefined} className={g.label ? `nav-group ${g.id === 'lapse' ? 'nav-group-lapse' : ''}` : ''}>
                {g.label && <span className="nav-cap">{g.label}</span>}
                <ul className="flex gap-1">
                  {g.tabs.map((id) => {
                    const t = TABS.find((x) => x.id === id)!
                    return (
                      <li key={t.id}>
                        <a
                          href={t.hash}
                          aria-current={tab === t.id ? 'page' : undefined}
                          className="inline-flex min-h-[40px] items-center border-b-[3px] border-transparent px-2.5 font-semibold text-ink-2 no-underline hover:text-ink aria-[current=page]:border-ink aria-[current=page]:text-ink"
                        >
                          {t.label}
                        </a>
                      </li>
                    )
                  })}
                </ul>
              </li>
            ))}
          </ul>
        </nav>
        <div className="ml-auto flex items-center gap-4 text-[15px]">
          {d && (
            <span className="num hidden text-ink-3 2xl:inline" title={d.forecast.mode === 'replay' ? 'A fast simulated clock over a real past day of prices' : 'Real time, real exchange prices'}>
              {d.forecast.mode === 'replay' ? `Replay ${fmtDayHM(d.forecast.now)}` : `${fmtHM(d.forecast.now)} IST`}
            </span>
          )}
          <button type="button" className="btn btn-small btn-quiet" onClick={theme.toggle}>{theme.now === 'dark' ? 'Day mode' : 'Night mode'}</button>
          <button type="button" className="btn btn-small btn-quiet" onClick={() => openWords()}>Words used here</button>
        </div>
      </header>
      <hr className="border-ink" />
      {d?.forecast.mode === 'replay' && (
        <Notice>
          Replay clock: this server’s clock runs {d.forecast.sim_scale} times faster than real time, over a real past day of prices. Times and prices on the pages that follow the server clock are replayed, not today’s.
        </Notice>
      )}

      <main id="main" tabIndex={-1} className="outline-none">
        {tab === 'home' ? <Home /> : tab === 'live' ? <Live /> : tab === 'prices' ? <Prices data={d} error={poll.error} /> : tab === 'whatif' ? <WhatIf /> : tab === 'measured' ? <Measured /> : <Try poll={poll} />}
      </main>
    </div>
  )
}

export default function App() {
  return (
    <GlossaryProvider>
      <Shell />
    </GlossaryProvider>
  )
}
