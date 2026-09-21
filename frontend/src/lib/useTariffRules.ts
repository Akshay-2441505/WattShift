import { useEffect, useMemo, useState } from 'react'
import { getTariff } from '../api'
import { dayBands } from './tariff'
import type { Tariff, TariffRule } from '../types'

// The MERC order for HT industrial, FY 2026-27. Used until (or unless) the API answers with the live copy.
const FALLBACK_RULES: TariffRule[] = [
  { zone: 'baseline', start_hour: 0, end_hour: 9, season: null, adj_pct: 0 },
  { zone: 'solar', start_hour: 9, end_hour: 17, season: 'apr_sep', adj_pct: -15 },
  { zone: 'solar', start_hour: 9, end_hour: 17, season: 'oct_mar', adj_pct: -25 },
  { zone: 'peak', start_hour: 17, end_hour: 24, season: null, adj_pct: 25 },
]
const FALLBACK_BASE = 8.44

const seasonNow = (): 'apr_sep' | 'oct_mar' => {
  const m = new Date().getMonth() + 1
  return m >= 4 && m <= 9 ? 'apr_sep' : 'oct_mar'
}

/** The real tariff as bands of the day, with the printed order as a fallback so the pictures never wait on the network. */
export function useTariffRules() {
  const [tariff, setTariff] = useState<Pick<Tariff, 'rules' | 'base_rate_rs_kwh' | 'season_now'> | null>(null)
  useEffect(() => {
    getTariff().then(setTariff).catch(() => setTariff(null))
  }, [])
  const rules = tariff?.rules ?? FALLBACK_RULES
  const base = tariff?.base_rate_rs_kwh ?? FALLBACK_BASE
  const season = tariff?.season_now ?? seasonNow()
  const bands = useMemo(() => dayBands(rules, season), [rules, season])
  return { bands, base }
}
