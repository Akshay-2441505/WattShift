import { useCallback, useEffect, useRef, useState } from 'react'
import { getSites, getSiteView } from './api'
import type { SiteInfo, SiteView } from './types'

export interface Polled<T> {
  data: T | null
  error: string | null // set while the latest poll fails; `data` keeps the last good value
  refresh: () => void
}

function usePolled<T>(fetcher: (() => Promise<T>) | null, intervalMs: number): Polled<T> {
  const [state, setState] = useState<{ data: T | null; error: string | null }>({ data: null, error: null })
  const tick = useRef<() => void>(() => {})
  useEffect(() => {
    if (!fetcher) return
    let alive = true
    const run = async () => {
      try {
        const data = await fetcher()
        if (alive) setState({ data, error: null })
      } catch (e) {
        if (alive) setState((s) => ({ ...s, error: e instanceof Error ? e.message : String(e) }))
      }
    }
    tick.current = run
    run()
    const id = setInterval(run, intervalMs)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [fetcher, intervalMs])
  const refresh = useCallback(() => tick.current(), [])
  return { ...state, refresh }
}

/** The sites this Wattshift knows. */
export const useSites = (intervalMs = 5000) => usePolled<SiteInfo[]>(getSites, intervalMs)

/** One site's whole page, every 2 seconds by default (the five-minute demo moves in seconds). */
export function useSitePoll(siteId: string | null, intervalMs = 2000): Polled<SiteView> {
  const fetcher = useCallback(() => getSiteView(siteId!), [siteId])
  return usePolled<SiteView>(siteId ? fetcher : null, intervalMs)
}
