import { useEffect, useState } from 'react'

/** True when the visitor's system asks for less motion. Moving pictures then show their final frame and stay still. */
export function useReducedMotion() {
  const [reduce, setReduce] = useState(() => (typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)').matches : false))
  useEffect(() => {
    if (typeof matchMedia !== 'function') return
    const q = matchMedia('(prefers-reduced-motion: reduce)')
    const on = () => setReduce(q.matches)
    q.addEventListener('change', on)
    return () => q.removeEventListener('change', on)
  }, [])
  return reduce
}
