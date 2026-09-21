import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchSnapshot } from './api'
import type { Snapshot } from './types'

export interface PollState {
  data: Snapshot | null
  error: string | null // set while the latest poll is failing; `data` keeps the last good snapshot
  refreshing: boolean
  refresh: () => void // fetch now, e.g. right after submitting a job
}

/** Polls the API. A refetch never blanks the screen: the previous snapshot stays until the next one arrives. */
export function usePoll(intervalMs = 5000): PollState {
  const [state, setState] = useState<Omit<PollState, 'refresh'>>({ data: null, error: null, refreshing: true })
  const alive = useRef(true)
  const tickRef = useRef<() => Promise<void>>(async () => {})

  useEffect(() => {
    alive.current = true
    const tick = async () => {
      setState((s) => ({ ...s, refreshing: true }))
      try {
        const data = await fetchSnapshot()
        if (alive.current) setState({ data, error: null, refreshing: false })
      } catch (e) {
        if (alive.current) setState((s) => ({ ...s, error: e instanceof Error ? e.message : String(e), refreshing: false }))
      }
    }
    tickRef.current = tick
    tick()
    const id = setInterval(tick, intervalMs)
    return () => {
      alive.current = false
      clearInterval(id)
    }
  }, [intervalMs])

  const refresh = useCallback(() => void tickRef.current(), [])
  return { ...state, refresh }
}
