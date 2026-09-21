// Plain-word meanings for every term the screens use. A visitor can open this from the header or from any dotted word.
export interface Entry {
  key: string
  term: string
  text: string
}

export const GLOSSARY: Entry[] = [
  { key: 'slot', term: 'Slot', text: 'A stretch of time when electricity is cheap. A held job waits at the gate for its slot, the way a plane waits for its take-off slot.' },
  { key: 'tariff', term: 'Time-of-day tariff', text: 'A price list that changes with the hour. In Maharashtra a factory pays 15% less from 9am to 5pm in April to September, and 25% more from 5pm to midnight.' },
  { key: 'held', term: 'Held job', text: 'A job that has been given a later start time in Slurm. Slurm will not start it earlier, even if the GPUs are free.' },
  { key: 'slurm', term: 'Slurm', text: 'Software that many GPU clusters use to line jobs up and decide when each one runs.' },
  { key: 'agent', term: 'Agent', text: 'A small program that sits next to Slurm. It reads the waiting jobs and, when allowed, sets start times. It can run only four kinds of command, and it logs every one.' },
  { key: 'shadow', term: 'Shadow mode', text: 'Watch only. Wattshift shows what it would do, and changes nothing in Slurm.' },
  { key: 'autonomous', term: 'Autonomous mode', text: 'Wattshift sets start times in Slurm on the jobs the owner has allowed it to move.' },
  { key: 'release', term: 'Release all', text: 'The emergency button. It sets every held job back to start now.' },
  { key: 'flexible', term: 'Flexible job', text: 'A job that can wait without harm, such as a training run or batch work, as opposed to a job someone is waiting on.' },
  { key: 'baseline', term: 'Baseline', text: 'What would have happened without Wattshift: the job starts when Slurm would have started it.' },
  { key: 'measured', term: 'Measured and modelled', text: 'Measured: start and end times come from Slurm’s own records. Modelled: the electricity a job uses is estimated (GPUs times kilowatts per GPU), not read from a meter. So the rupee figures are estimates, and the percentages are the safer thing to quote.' },
  { key: 'timelapse', term: 'Time-lapse', text: 'In the live demo, 2 minutes stand for 1 hour of the tariff clock, so a whole day fits into minutes and a wait of several hours shows up as a few minutes. The jobs and Slurm are real; only the tariff clock is sped up.' },
  { key: 'realspeed', term: 'Real speed', text: 'Normal clocks and the real tariff. A job held for a cheap window may wait for hours before it starts.' },
  { key: 'gpuhour', term: 'GPU-hour', text: 'One GPU running for one hour. Measured costs are quoted per GPU-hour, so they do not depend on how long the short demo run lasted.' },
  { key: 'iex', term: 'IEX', text: 'The Indian Energy Exchange, where electricity is traded a day ahead. Its prices feed the forecast.' },
  { key: 'backtest', term: 'Backtest', text: 'Replaying a past list of jobs to see what timing them differently would have saved.' },
]

export const termFor = (key: string) => GLOSSARY.find((g) => g.key === key)
