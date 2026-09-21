import { DragDay, HeroDay } from '../components/DayViews'
import { Section, Term, useGlossary } from '../components/ui'

const HOW: { verb: string; text: React.ReactNode }[] = [
  {
    verb: 'Watch',
    text: (
      <>
        A small program, the <Term k="agent">agent</Term>, sits next to the company’s job queue (<Term k="slurm">Slurm</Term>) and reads which jobs are waiting.
      </>
    ),
  },
  {
    verb: 'Plan',
    text: 'The cloud picks the cheapest start time for each job that can wait. It never picks one that would break the job’s limit, and it moves a job only if that makes the bill lower.',
  },
  {
    verb: 'Hold',
    text: 'It sets that start time inside Slurm itself. From then on Slurm holds the job, even when the GPUs are free, and starts it on time.',
  },
  {
    verb: 'Measure',
    text: 'Once the job has run, it reads Slurm’s own start and end times and works out what waiting saved.',
  },
]

const SAFETY: { title: string; text: React.ReactNode }[] = [
  {
    title: 'Watch first',
    text: (
      <>
        <Term k="shadow">Shadow mode</Term> only reads. It shows what Wattshift would do and changes nothing.
      </>
    ),
  },
  {
    title: 'Slurm keeps the promise',
    text: 'The start time lives in Slurm. If Wattshift crashes or is switched off, held jobs still start on time.',
  },
  {
    title: 'One emergency button',
    text: (
      <>
        <Term k="release">Release all</Term> sets every held job back to start now. It works even when the cloud is down.
      </>
    ),
  },
  {
    title: 'Four commands, all logged',
    text: 'The agent can run four kinds of Slurm command and nothing else, and it writes down every one it runs.',
  },
  {
    title: 'Owners stay in charge',
    text: 'A job whose owner changes its start time is left alone. So are array jobs and jobs that depend on other jobs.',
  },
]

const LEDGER: { label: string; text: React.ReactNode }[] = [
  {
    label: 'Real',
    text: 'Slurm, the agent, the cloud’s planning, the start times Slurm enforces, and the start and end times the saving is worked out from. Checked end to end on a real Slurm: 33 of 33 checks passed.',
  },
  {
    label: 'Modelled',
    text: (
      <>
        The electricity a job uses (GPUs times 1.25 kW each), and so every rupee figure. It has not been compared with a real electricity bill, so quote the percentages, not the rupees. See <Term k="measured">measured and modelled</Term>.
      </>
    ),
  },
  {
    label: 'Demo only',
    text: (
      <>
        The sped-up tariff clock (<Term k="timelapse">time-lapse</Term>), the fake GPUs in Docker, and jobs that run seconds. It has not been run on a real customer’s cluster. On a real AI-training log the saving is small, about 0.2%, up to 2%, because a few very long jobs use most of the energy.
      </>
    ),
  },
]

const NEXT: { href: string; title: string; text: string }[] = [
  { href: '#/live', title: 'Live demo', text: 'Time-lapse. Watch real jobs get held and started, and switch modes yourself.' },
  { href: '#/prices', title: 'Prices today', text: 'Real speed. See which hours are cheap right now and what the price forecast looks like.' },
  { href: '#/whatif', title: 'What if', text: 'Real speed, past data. Replay a real GPU cluster’s past jobs and see what timing would have saved.' },
  { href: '#/measured', title: 'Measured', text: 'Real speed, real GPU. The same job run two ways on a Kaggle GPU, with its power read from the GPU and priced at the Maharashtra tariff.' },
  { href: '#/try', title: 'Try it', text: 'Real speed. Give it a job and a deadline and it picks the cheapest window.' },
]

export function Home() {
  const openWords = useGlossary()
  return (
    <>
      <section className="grid items-start gap-x-14 gap-y-10 pb-16 pt-8 sm:pt-12 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="lg:pt-6">
          <h1 className="text-[clamp(2.3rem,4.8vw,3.8rem)]">Run GPU jobs when power is cheap.</h1>
          <p className="mt-6 max-w-[32ch] text-[1.3125rem] leading-snug text-ink-2">
            Wattshift holds jobs that can wait until a cheaper power slot opens, then measures what that saved.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <a href="#/live" className="btn btn-primary">Watch the time-lapse demo</a>
            <a href="#/try" className="btn">Try it at real speed</a>
          </div>
          <p className="mt-6 max-w-[44ch] text-[15px] text-ink-3">
            For a busy GPU cluster, electricity is often the biggest running cost, and India charges different prices at different hours. This is a working project that runs
            jobs in the cheap hours.
          </p>
        </div>
        <HeroDay />
      </section>

      <Section
        id="idea"
        title="The same job costs different amounts at different hours"
        note={
          <>
            A job is like a plane waiting at the gate: it can leave now, or wait for a better <Term k="slot">slot</Term>. In Maharashtra the <Term k="tariff">tariff</Term> makes some hours much cheaper. Drag the start time and watch the price.
          </>
        }
      >
        <DragDay />
      </Section>

      <Section
        id="speeds"
        title="Two ways to see it: sped up, or at real speed"
        note="Both show the same mechanism. They differ only in speed, and every page says which one it is, in its header tab and in a note under its title."
      >
        <div className="grid gap-x-14 gap-y-10 md:grid-cols-2">
          <div className="border-t-2 border-dashed border-ink pt-4">
            <h3 className="text-[1.5rem] [font-stretch:112%] [font-weight:800]">Time-lapse demo</h3>
            <p className="mt-1 font-semibold">About 5 minutes. Sped up.</p>
            <ul className="mt-3 max-w-[46ch] space-y-2 text-ink-2">
              <li>A real Slurm queue with real jobs and real start times.</li>
              <li>The tariff clock runs 60 times faster: 2 minutes stand for 1 hour, so a 12-hour wait shows up as 24 minutes.</li>
              <li>Best for seeing the whole idea work in one sitting, including switching Wattshift off.</li>
            </ul>
            <a href="#/live" className="btn btn-primary mt-5">Watch the time-lapse demo</a>
          </div>
          <div className="border-t-2 border-solid border-ink pt-4">
            <h3 className="text-[1.5rem] [font-stretch:112%] [font-weight:800]">Real speed</h3>
            <p className="mt-1 font-semibold">Normal clocks. Hours, not minutes.</p>
            <ul className="mt-3 max-w-[46ch] space-y-2 text-ink-2">
              <li>The real tariff and the exchange’s own prices.</li>
              <li>A job you submit is held for a real cheap window, which can be hours away, and then runs.</li>
              <li>Best for seeing what it would actually do over a day.</li>
            </ul>
            <div className="mt-5 flex flex-wrap gap-3">
              <a href="#/try" className="btn">Try it at real speed</a>
              <a href="#/prices" className="btn btn-quiet">See today’s prices</a>
            </div>
          </div>
        </div>
        <div className="mt-10 max-w-[64ch] border-t border-line pt-5">
          <h3 className="text-lg">It has also been run at true speed</h3>
          <p className="mt-2 text-ink-2">
            The end-to-end proof waited for a real change of tariff hour on a real Slurm, with no sped-up clock. Three jobs were held until the cheap window opened and started 11, 12 and 32 seconds after their set time, with the agent and the cloud both switched off. All 33 checks passed. The full report is in the project’s docs folder.
          </p>
        </div>
      </Section>

      <Section id="how" title="How it works" note="Four steps, and only the jobs a company has said can wait are ever touched.">
        <ol>
          {HOW.map((h) => (
            <li key={h.verb} className="grid gap-x-10 gap-y-1 border-t border-line py-5 first:border-t-0 sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)]">
              <h3 className="text-[1.75rem] leading-none [font-stretch:112%] [font-weight:800]">{h.verb}</h3>
              <p className="max-w-[58ch] text-ink-2">{h.text}</p>
            </li>
          ))}
        </ol>
      </Section>

      <Section title="If Wattshift stops, Slurm keeps the promise" note="This is the part a cautious company will ask about first.">
        <ul className="max-w-[64ch] space-y-4">
          {SAFETY.map((s) => (
            <li key={s.title}>
              <strong>{s.title}.</strong> <span className="text-ink-2">{s.text}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="What is real, and what is not" note="Every figure on this site says which of these it is.">
        <dl>
          {LEDGER.map((l) => (
            <div key={l.label} className="grid gap-x-10 gap-y-1 border-t border-line py-5 first:border-t-0 sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)]">
              <dt className="text-[1.5rem] leading-none [font-stretch:112%] [font-weight:800]">{l.label}</dt>
              <dd className="max-w-[62ch] text-ink-2">{l.text}</dd>
            </div>
          ))}
        </dl>
      </Section>

      <Section title="Where to go next">
        <ul>
          {NEXT.map((n) => (
            <li key={n.href} className="border-t border-line first:border-t-0">
              <a href={n.href} className="grid gap-x-10 gap-y-1 py-5 no-underline hover:bg-recess sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)]">
                <span className="text-[1.5rem] leading-none underline decoration-2 underline-offset-[6px] [font-stretch:112%] [font-weight:800]">{n.title}</span>
                <span className="max-w-[52ch] text-ink-2">{n.text}</span>
              </a>
            </li>
          ))}
        </ul>
        <p className="mt-6 text-[15px] text-ink-3">
          Not sure what a word means? <button type="button" className="term" onClick={() => openWords()}>Open the list of words used here</button>.
        </p>
      </Section>

      <footer className="border-t border-ink py-8 text-[15px] text-ink-3">
        <p className="max-w-[64ch]">
          Wattshift is a project, not a company, and the name is a placeholder. Prices come from the Indian Energy Exchange (<Term k="iex">IEX</Term>); the tariff is the MERC order for high-tension industrial users in Maharashtra, FY 2026-27.
        </p>
      </footer>
    </>
  )
}
