import { useCallback, useEffect, useState } from 'react'

type Side = 'light' | 'dark'
const KEY = 'wattshift.theme'

const stored = (): Side | null => {
  const q = new URLSearchParams(location.search).get('theme') // also lets a link or a screenshot pick a side
  if (q === 'light' || q === 'dark') return q
  try {
    const s = localStorage.getItem(KEY)
    return s === 'light' || s === 'dark' ? s : null
  } catch {
    return null // storage can be blocked; the choice just is not remembered
  }
}

/** Day or night. Follows the system until the visitor picks a side, then remembers it. */
export function useTheme() {
  const [chosen, setChosen] = useState<Side | null>(stored)
  useEffect(() => {
    const root = document.documentElement
    if (chosen) root.dataset.theme = chosen
    else delete root.dataset.theme
    try {
      if (chosen) localStorage.setItem(KEY, chosen)
    } catch {
      /* not remembered */
    }
  }, [chosen])
  const systemDark = typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches
  const now: Side = chosen ?? (systemDark ? 'dark' : 'light')
  const toggle = useCallback(() => setChosen(now === 'dark' ? 'light' : 'dark'), [now])
  return { now, toggle }
}
