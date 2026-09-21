// Small pure helpers for the Backtest page: the plain-language insight and form -> API request.

export interface LengthRow {
  label: string
  jobs_share: number
  energy_share: number
}

// The scheduler moves jobs up to 3 hours, i.e. the first three length buckets.
const MOVABLE_BUCKETS = 3

export function insight(rows: LengthRow[]) {
  const movable = rows.slice(0, MOVABLE_BUCKETS)
  return {
    top: rows.reduce((best, r) => (r.energy_share > best.energy_share ? r : best), rows[0] ?? { label: '', jobs_share: 0, energy_share: 0 }),
    movableEnergyShare: movable.reduce((n, r) => n + r.energy_share, 0),
    movableJobsShare: movable.reduce((n, r) => n + r.jobs_share, 0),
  }
}

export interface Draft {
  flexiblePct: string
  slackHours: string
  gpus: string
  kwPerGpu: string
  capacityPct: string
}

const num = (s: string) => (s.trim() === '' ? NaN : Number(s))

export function validateAssumptions(d: Draft): string[] {
  const errors: string[] = []
  const flex = num(d.flexiblePct)
  if (!(flex >= 0 && flex <= 100)) errors.push('Jobs that can wait must be between 0 and 100 percent.')
  const slack = num(d.slackHours)
  if (!(slack >= 0 && slack <= 72)) errors.push('How long jobs can wait must be between 0 and 72 hours.')
  const gpus = num(d.gpus)
  if (!(Number.isInteger(gpus) && gpus >= 1)) errors.push('Fleet size must be a whole number of GPUs, at least 1.')
  const kw = num(d.kwPerGpu)
  if (!(kw > 0 && kw <= 10)) errors.push('Power per GPU must be above 0 and at most 10 kW.')
  const cap = num(d.capacityPct)
  if (!(cap > 0 && cap <= 100)) errors.push('Spare capacity must be above 0 and at most 100 percent.')
  return errors
}

export function toRequest(d: Draft, source: 'sample' | 'upload', csv: string | null, retime: boolean) {
  return {
    source,
    ...(source === 'upload' && csv ? { csv } : {}),
    retime,
    assumptions: {
      flexible_share: num(d.flexiblePct) / 100,
      slack_hours: num(d.slackHours),
      kw_per_gpu: num(d.kwPerGpu),
      cluster_gpus: num(d.gpus),
      shift_capacity_share: num(d.capacityPct) / 100,
    },
  }
}
