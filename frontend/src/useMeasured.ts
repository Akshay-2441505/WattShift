import { useEffect, useState } from 'react'
import { getMeasured } from './api'
import type { Measured } from './types'

/** Polls the measured runs. A failed poll keeps the last good answer and reports the error. */
export function useMeasured(intervalMs = 10000): { data: Measured | null; error: boolean } {
  const [state, setState] = useState<{ data: Measured | null; error: boolean }>({ data: null, error: false })
  useEffect(() => {
    let alive = true
    const tick = () =>
      document.hidden ? undefined : getMeasured().then(
        (data) => alive && setState({ data, error: false }),
        () => alive && setState((s) => ({ ...s, error: true })),
      )
    tick()
    const id = setInterval(tick, intervalMs)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [intervalMs])
  return state
}
