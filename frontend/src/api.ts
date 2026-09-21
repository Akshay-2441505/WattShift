import type { BacktestRun, BacktestSample, Forecast, Job, Measured, Savings, SiteInfo, SiteView, Snapshot, Tariff } from './types'
export type { Job }

// Dev: /api is proxied to FastAPI by Vite. Deployed: set VITE_API_URL to the backend origin.
const BASE = import.meta.env.VITE_API_URL ?? '/api'

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`)
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`)
  return r.json()
}

export interface NewJob {
  duration_minutes: number
  deadline: string
  provider: 'kaggle'
  power_kw?: number
}

export type SubmitResult =
  | { kind: 'placed'; job: Job } // 201: held for a window
  | { kind: 'queued'; job: Job; detail: string } // 409: stored, but nothing fits before the deadline yet
  | { kind: 'invalid'; messages: string[] } // 422
  | { kind: 'auth' } // 401: the server needs an X-API-Key
  | { kind: 'error'; message: string }

/** FastAPI reports validation errors either as a string or as a list of { msg, loc }. */
function detailMessages(detail: unknown): string[] {
  if (typeof detail === 'string') return [detail]
  if (Array.isArray(detail)) return detail.map((d) => (typeof d?.msg === 'string' ? `${(d.loc ?? []).slice(1).join('.')}: ${d.msg}` : String(d)))
  return ['The server rejected this job.']
}

export async function submitJob(job: NewJob, apiKey?: string): Promise<SubmitResult> {
  try {
    const r = await fetch(`${BASE}/jobs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(apiKey ? { 'X-API-Key': apiKey } : {}) },
      body: JSON.stringify(job),
    })
    const body = await r.json().catch(() => ({}))
    if (r.status === 201) return { kind: 'placed', job: body }
    if (r.status === 409) return { kind: 'queued', job: body.job, detail: body.detail }
    if (r.status === 401) return { kind: 'auth' }
    if (r.status === 422) return { kind: 'invalid', messages: detailMessages(body.detail) }
    return { kind: 'error', message: `The server answered HTTP ${r.status}.` }
  } catch {
    return { kind: 'error', message: 'Could not reach the Wattshift API. Check that it is running, then try again.' }
  }
}

export const getBacktestSample = () => get<BacktestSample>('/backtest/sample')
export const getBacktest = (id: string) => get<BacktestRun>(`/backtest/${id}`)

export type StartResult = { kind: 'started'; id: string } | { kind: 'invalid'; messages: string[] } | { kind: 'auth' } | { kind: 'busy' } | { kind: 'error'; message: string }

export async function startBacktest(body: unknown, apiKey?: string): Promise<StartResult> {
  try {
    const r = await fetch(`${BASE}/backtest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(apiKey ? { 'X-API-Key': apiKey } : {}) },
      body: JSON.stringify(body),
    })
    const j = await r.json().catch(() => ({}))
    if (r.status === 202) return { kind: 'started', id: j.id }
    if (r.status === 401) return { kind: 'auth' }
    if (r.status === 429) return { kind: 'busy' }
    if (r.status === 422) return { kind: 'invalid', messages: detailMessages(j.detail) }
    return { kind: 'error', message: `The server answered HTTP ${r.status}.` }
  } catch {
    return { kind: 'error', message: 'Could not reach the Wattshift API. Check that it is running, then try again.' }
  }
}

export async function fetchSnapshot(): Promise<Snapshot> {
  const [forecast, tariff, jobs, savings] = await Promise.all([
    get<Forecast>('/forecast?hours=24'),
    get<Tariff>('/tariff'),
    get<Job[]>('/jobs'),
    get<Savings>('/savings/summary'),
  ])
  return { forecast, tariff, jobs, savings, fetchedAt: Date.now() }
}

export const getTariff = () => get<Tariff>('/tariff')
export const getHealth = () => get<{ ok: boolean; can_run_jobs: boolean }>('/health')
export const getMeasured = () => get<Measured>('/measured')
export const getSites = () => get<SiteInfo[]>('/sites')
export const getSiteView = (id: string) => get<SiteView>(`/sites/${id}/view`)

export type OpResult = { kind: 'ok' } | { kind: 'auth' } | { kind: 'error'; message: string }

async function post(path: string, body: unknown, apiKey?: string): Promise<OpResult> {
  try {
    const r = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(apiKey ? { 'X-API-Key': apiKey } : {}) },
      body: JSON.stringify(body),
    })
    if (r.ok) return { kind: 'ok' }
    if (r.status === 401) return { kind: 'auth' }
    return { kind: 'error', message: `The server answered HTTP ${r.status}.` }
  } catch {
    return { kind: 'error', message: 'Could not reach the Wattshift API. Check that it is running, then try again.' }
  }
}

export const setSiteMode = (id: string, mode: 'shadow' | 'autonomous', apiKey?: string) => post(`/sites/${id}/mode`, { mode }, apiKey)
export const releaseAll = (id: string, on: boolean, apiKey?: string) => post(`/sites/${id}/release-all`, { on }, apiKey)
