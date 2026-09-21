// Shapes returned by the Wattshift API (backend/app/main.py). Zone names are the API's.
export type Zone = 'solar' | 'baseline' | 'peak'
export type JobStatus = 'queued' | 'scheduled' | 'running' | 'done' | 'failed'

export interface Block {
  ts: string
  iex_price: number // Rs/MWh
  tod_zone: Zone
  tod_multiplier: number
  billed_rs_kwh: number // what the DISCOM bill charges
  effective_rate: number // Rs/MWh x multiplier; what the scheduler ranks on
  carbon_index: number // 0-100, MODELED
}

export interface Forecast {
  now: string
  mode: 'live' | 'replay'
  sim_scale: number
  source: string
  carbon_modeled: boolean
  blocks: Block[]
}

export interface TariffRule {
  zone: Zone
  start_hour: number
  end_hour: number
  season: 'apr_sep' | 'oct_mar' | null
  adj_pct: number
}

export interface Tariff {
  base_rate_rs_kwh: number
  verified: boolean // checked against the MERC order AND still inside the period it covers
  valid_until: string
  source: string
  notes: string[]
  season_now: 'apr_sep' | 'oct_mar'
  rules: TariffRule[]
}

export interface Job {
  id: string
  status: JobStatus
  provider: string
  duration_minutes: number
  deadline: string
  power_kw: number
  assigned_window_start: string | null
  tod_zone: Zone | null
  baseline_cost_rs: number | null
  planned_cost_rs: number | null
  submitted_at: string
  executed_at: string | null
  failure_reason: string | null
  actual_cost_rs: number | null
  saved_rs: number | null
}

export interface Savings {
  total: number
  today: number
  week: number
  daily_avg: number
  daily: { date: string; saved: number }[]
  jobs_counted: number
  baseline_total: number
  pct_saved: number | null
}

export interface BacktestSample {
  jobs: number
  fleet_gpus: number
  price_window: { from: string; to: string }
  source: string
}

export interface BacktestResult {
  assumptions: { flexible_share: number; slack_hours: number; kw_per_gpu: number; cluster_gpus: number; shift_capacity_share: number; max_shiftable_min: number }
  period: { from: string; to: string }
  tariff: string
  retimed: boolean
  totals: {
    jobs: number
    kwh: number
    baseline_rs: number
    scheduled_rs: number
    saved_rs: number
    pct_saved: number | null
    n_eligible: number
    n_flexible: number
    n_placed: number
    n_unplaced: number
    avg_delay_hours: number
    flexible_energy_share: number
    max_block_gpus: number
  }
  energy_by_length: { label: string; jobs_share: number; energy_share: number }[]
  kwh_by_zone_before: Record<Zone, number>
  kwh_by_zone_after: Record<Zone, number>
  sensitivity: { flexible_share: number; slack_hours: number; pct_saved: number | null; saved_rs: number }[] | null
}

export interface BacktestRun {
  id: string
  status: 'queued' | 'running' | 'done' | 'error'
  stage: 'queued' | 'main' | 'scenarios' | 'finished'
  error: string | null
  result: BacktestResult | null
}

export interface Snapshot {
  forecast: Forecast
  tariff: Tariff
  jobs: Job[]
  savings: Savings
  fetchedAt: number
}

// --- Live cluster (a customer site managed by the agent): backend/app/site_views.py ---
export type DisplayState =
  | 'seen' | 'would_hold' | 'held' | 'runs_normally' | 'no_window' | 'skipped'
  | 'owner_changed' | 'left_alone' | 'released' | 'running' | 'done' | 'ended'

export interface SiteInfo {
  id: string
  company: string
  name: string
  mode: 'shadow' | 'autonomous'
  release_all: boolean
  gpus: number
  last_seen_at: string | null
  agent_version: string | null
}

export interface SiteJob {
  ref: string
  state: string // Slurm's state, normalised: PENDING | RUNNING | COMPLETED | ...
  plan_status: string
  display: DisplayState
  note: string | null
  gpus: number | null
  time_limit_min: number | null
  max_wait_min: number | null
  submit_time: string
  baseline_start: string | null // where it would have started without Wattshift
  planned_start: string | null
  applied_start: string | null // the start time the agent confirmed in Slurm
  actual_start: string | null
  actual_end: string | null
  baseline_cost: number | null // Rs, modeled
  planned_cost: number | null
  actual_cost: number | null
  saved: number | null // measured, only once the job has finished
}

export interface SiteView {
  site: SiteInfo & { agent_mode: string | null; tariff: string }
  now: string
  summary: {
    saved: number // measured, from real start and end times
    baseline: number
    pct_saved: number | null
    jobs_measured: number
    potential: number // the plan for jobs not finished yet: an estimate, never part of `saved`
    counts: Partial<Record<DisplayState, number>>
  }
  jobs: SiteJob[]
  activity: { at: string; actor: string; event: string; ref: string | null; text: string }[]
  zones: { start: string; end: string; zone: Zone }[]
}

// --- Measured power on the Kaggle GPU: backend/app/measured.py ---
export interface MeasuredRun {
  status: string // scheduled | running | done | failed, or the job's own status while its real run has not started
  started_at: string | null
  planned_start: string | null // the window Wattshift chose, for a real run that has not started
  zone: Zone | null
  avg_watts: number | null
  energy_wh: number | null
  rs_per_gpu_hour: number | null
}

export interface MeasuredJob {
  job_id: string
  submitted_at: string
  job_status: JobStatus
  complete: boolean // both runs finished with a power reading
  simulated: boolean // made on the replay clock: the power is measured, the hour it was priced at is replayed
  without: MeasuredRun // started at once, as if Wattshift were not there
  with: MeasuredRun // started at the window Wattshift chose
  saved_rs_per_gpu_hour: number | null
  pct_saved: number | null
}

export interface Measured {
  basis: { gpu: string; power_limit_w: number; per: string; tariff: string; base_rate_rs_kwh: number }
  summary: {
    pairs: number
    simulated_pairs: number
    waiting: number
    avg_watts_without: number | null
    avg_watts_with: number | null
    rs_per_gpu_hour_without: number | null
    rs_per_gpu_hour_with: number | null
    saved_rs_per_gpu_hour: number | null
    pct_saved: number | null
  }
  jobs: MeasuredJob[]
}
