import { useEffect, useState } from 'react'
import { releaseAll, setSiteMode, type OpResult } from '../api'
import type { SiteView } from '../types'
import { agentStatus } from '../lib/live'
import { ErrorList, Field, Notice, Term } from './ui'

const KEY_STORE = 'wattshift.apiKey' // shared with the Try it form
const readKey = () => {
  try {
    return sessionStorage.getItem(KEY_STORE) ?? ''
  } catch {
    return ''
  }
}

/** The site's name, whether its agent is reporting, the mode switch, and the emergency button. */
export function LiveControls({ view, refresh }: { view: SiteView; refresh: () => void }) {
  const { site } = view
  const agent = agentStatus(view)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [needKey, setNeedKey] = useState(false)
  const [apiKey, setApiKey] = useState(readKey)
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    if (!confirming) return
    const t = setTimeout(() => setConfirming(false), 5000) // an unconfirmed emergency button disarms itself
    return () => clearTimeout(t)
  }, [confirming])

  async function run(op: (key?: string) => Promise<OpResult>) {
    setBusy(true)
    setMessage(null)
    const r = await op(apiKey || undefined)
    setBusy(false)
    if (r.kind === 'ok') {
      setNeedKey(false)
      try {
        if (apiKey) sessionStorage.setItem(KEY_STORE, apiKey)
      } catch {
        /* storage can be blocked; the key just is not remembered */
      }
      refresh()
    } else if (r.kind === 'auth') {
      setNeedKey(true)
      setMessage(apiKey ? 'That API key was not accepted.' : 'This server needs an API key for operator actions.')
    } else setMessage(r.message)
  }

  const modeButton = (mode: 'shadow' | 'autonomous', label: string) => (
    <button type="button" disabled={busy} aria-pressed={site.mode === mode} onClick={() => site.mode !== mode && run((k) => setSiteMode(site.id, mode, k))}>
      {label}
    </button>
  )

  return (
    <div className="border-t border-ink py-5">
      <div className="flex flex-wrap items-start justify-between gap-x-10 gap-y-5">
        <div className="min-w-0">
          <h2 className="text-[clamp(1.5rem,3vw,2.1rem)]">
            {site.company} {site.name}
          </h2>
          <p className="mt-1 text-ink-2">
            {site.gpus} GPUs, {site.tariff.replace('TEST E2E', 'demo tariff')}. <span className="num text-[15px]">{agent.label}</span>
          </p>
        </div>
        <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
          <div>
            <div role="group" aria-label="Mode" className="seg">
              {modeButton('shadow', 'Shadow')}
              {modeButton('autonomous', 'Autonomous')}
            </div>
            <p className="mt-2 max-w-[34ch] text-[15px] text-ink-3">
              {site.mode === 'shadow' ? (
                <>
                  <Term k="shadow">Shadow</Term>: watching and planning. Nothing in Slurm changes.
                </>
              ) : (
                <>
                  <Term k="autonomous">Autonomous</Term>: setting start times in Slurm on the jobs it may move.
                </>
              )}
            </p>
          </div>
          <div>
            <button
              type="button"
              className="btn btn-danger"
              data-armed={confirming}
              disabled={busy || site.release_all}
              onClick={() => {
                if (confirming) {
                  setConfirming(false)
                  void run((k) => releaseAll(site.id, true, k))
                } else setConfirming(true)
              }}
            >
              {confirming ? 'Click again to release everything' : 'Release all held jobs'}
            </button>
            <p className="mt-2 max-w-[30ch] text-[15px] text-ink-3">Sets every held job back to start now.</p>
          </div>
        </div>
      </div>

      {site.release_all && (
        <Notice>
          The emergency button is on: every held job was set back to start now, and planning is paused for this site.{' '}
          <button type="button" disabled={busy} className="btn btn-small !min-h-[32px] border-board !text-board" onClick={() => void run((k) => releaseAll(site.id, false, k))}>
            Turn it off
          </button>
        </Notice>
      )}

      {(message || needKey) && (
        <div className="mt-4 max-w-md space-y-3">
          {message && <ErrorList errors={[message]} />}
          {needKey && (
            <Field id="live-key" label="API key" hint="Kept in this browser tab only.">
              <input id="live-key" type="password" autoComplete="off" spellCheck={false} value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="field" />
            </Field>
          )}
        </div>
      )}
    </div>
  )
}
