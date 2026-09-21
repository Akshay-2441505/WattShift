// Job-form helpers. Everything the operator types is IST, whatever time zone their browser is in, because the
// tariff zones and deadlines are IST. India has no DST, so a fixed +5:30 offset is exact.
const OFFSET_MS = 5.5 * 3600_000
const INPUT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/

export const MAX_DURATION_MIN = 180 // mirrors the API's limit

/** A <input type="datetime-local"> value, read as IST, as an ISO instant. Null if empty or not a real date. */
export function deadlineToIso(local: string): string | null {
  if (!INPUT.test(local)) return null
  const t = new Date(`${local}:00+05:30`)
  return Number.isNaN(t.getTime()) ? null : t.toISOString()
}

/** An instant as a <input type="datetime-local"> value in IST ("YYYY-MM-DDTHH:mm"). */
export const istInputValue = (iso: string) => new Date(new Date(iso).getTime() + OFFSET_MS).toISOString().slice(0, 16)

export const hoursAhead = (nowIso: string, hours: number) => istInputValue(new Date(new Date(nowIso).getTime() + hours * 3600_000).toISOString())

/** Tomorrow (IST) at a given time. */
export function tomorrowAt(nowIso: string, hour: number, minute = 0): string {
  const today = istInputValue(nowIso).slice(0, 10)
  const next = new Date(`${today}T00:00:00Z`)
  next.setUTCDate(next.getUTCDate() + 1)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${next.toISOString().slice(0, 10)}T${p(hour)}:${p(minute)}`
}

export interface JobDraft {
  durationMin: number
  deadlineLocal: string
  powerKw: number | null // null = leave to the server's default
  nowIso: string // the dashboard's clock (simulated in replay mode)
}

/** Friendly client-side checks; the API validates again and remains the source of truth. */
export function validateJob(d: JobDraft): string[] {
  const errors: string[] = []
  const durationOk = Number.isFinite(d.durationMin) && d.durationMin >= 1 && d.durationMin <= MAX_DURATION_MIN
  if (!durationOk) errors.push(`Duration must be between 1 and ${MAX_DURATION_MIN} minutes.`)
  else if (!Number.isInteger(d.durationMin)) errors.push('Duration must be a whole number of minutes.')

  const deadline = deadlineToIso(d.deadlineLocal)
  if (!deadline) errors.push('Choose a deadline.')
  else if (durationOk && new Date(deadline).getTime() < new Date(d.nowIso).getTime() + d.durationMin * 60_000)
    errors.push('The deadline leaves too little time for the job to finish.')

  if (d.powerKw !== null && !(d.powerKw > 0 && d.powerKw <= 1000)) errors.push('Power draw must be above 0 and at most 1,000 kW.')
  return errors
}
