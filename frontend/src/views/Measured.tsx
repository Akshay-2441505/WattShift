import { MeasuredSummary } from '../components/Measured'
import { PageHead, SpeedNote, Term } from '../components/ui'
import { useMeasured } from '../useMeasured'

export function Measured() {
  const m = useMeasured()
  return (
    <>
      <PageHead title="Measured">
        What the same job costs in electricity with and without Wattshift, using power read from a real GPU while it ran.
      </PageHead>
      <SpeedNote kind="real" title="Real GPU, real hours unless marked.">
        The runs are real Kaggle GPU runs. On the real clock a pair takes as long as the wait for a cheap hour, so a quick pair comes from the replay clock, and the page says when one did. Only one GPU model is measured, and the
        tariff is Maharashtra’s. Each job’s two runs are listed on the <a href="#/try">Try it</a> page. The <Term k="timelapse">time-lapse</Term> demo does not use them.
      </SpeedNote>
      <MeasuredSummary data={m.data} error={m.error} />
    </>
  )
}
