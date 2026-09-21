// All dashboard times are IST. India has no DST, so a fixed +5:30 offset is exact.
const OFFSET_MS = 5.5 * 3600_000
const WEEKDAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const pad = (n: number) => String(n).padStart(2, '0')

const ist = (iso: string) => new Date(new Date(iso).getTime() + OFFSET_MS) // read with the getUTC* methods

export const istHour = (iso: string) => ist(iso).getUTCHours()
export const istFraction = (iso: string) => {
  const d = ist(iso)
  return d.getUTCHours() + d.getUTCMinutes() / 60
}
export const fmtHM = (iso: string) => {
  const d = ist(iso)
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
}
export const fmtDayHM = (iso: string) => `${WEEKDAY[ist(iso).getUTCDay()]} ${fmtHM(iso)}`
export const fmtDay = (iso: string) => {
  const d = ist(iso)
  return `${WEEKDAY[d.getUTCDay()]} ${d.getUTCDate()}`
}
/** Start of the IST hour containing `ms` (epoch ms). */
export const istHourStart = (ms: number) => Math.floor((ms + OFFSET_MS) / 3600_000) * 3600_000 - OFFSET_MS

export const fmtHMS = (iso: string) => {
  const d = ist(iso)
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
}
