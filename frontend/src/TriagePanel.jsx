import { useEffect, useState } from 'react'
import { api } from './api.js'
import { ts } from './format.js'
import {
  AUTO_REVIEW_LEDE, AUTO_REVIEW_OFF_TIP, AUTO_REVIEW_ON_TIP, tallyLine,
} from './securityCopy.js'
import { notifyError } from './notify.js'
import { Button, Toggle } from './components/index.js'
import { State } from './securityParts.jsx'

// Control strip for Auto review — the isolated triage reviewer
// (backend/reviewer.py; "triage" stays the internal name) — the first section
// of the Security page. It does not list the flagged
// hosts/alerts itself — those live once, as the ⚑ rows in the queue sections
// below. What remains: the Auto/Manual switch (beside the heading), a
// run-now button, the last
// sweep's tally, and the reviewer's recent autonomous approves/acks with
// one-click undo.
//
// It wears the same section head as the queues below it (sbx-sec-head → h3 +
// count chip + right-aligned actions) so the page reads as one list of things
// rather than a foreign card sitting on top of one. The two-line explanation
// under the head is back (WEB-09): what reviews, what it may approve, that it
// costs tokens, where undo is. It is short enough to read every visit.
//
// "Review now" only exists when something is untriaged — the sweeper keeps
// that at zero, so a permanently greyed button was the whole card's read.
//
// EVERY item string here — hostnames, the reviewer's own reasons (a model
// output derived from untrusted input) — is UNTRUSTED and is rendered as
// plain text nodes only, never through <Md>.

export default function TriagePanel() {
  const [s, setS] = useState(null)
  const [busy, setBusy] = useState(false)
  const [logOpen, setLogOpen] = useState(false)

  const load = () => api('/api/reviewer').then(setS).catch(() => {})
  useEffect(() => {
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [])
  // poll faster while a run is in flight so the summary lands promptly
  useEffect(() => {
    if (!s?.running) return
    const t = setInterval(load, 3000)
    return () => clearInterval(t)
  }, [s?.running])

  if (!s) return null
  const flagged = (s.flagged_hosts?.length || 0) + (s.flagged_alerts?.length || 0)
  const untriaged = (s.untriaged?.hosts || 0) + (s.untriaged?.alerts || 0)
  const last = s.last_run

  async function act(path) {
    setBusy(true)
    try { await api(path, { method: 'POST' }); await load() }
    catch (e) { notifyError(e) }
    setBusy(false)
    window.dispatchEvent(new Event('jarvis-files-changed'))
  }
  // optimistic: the thumb moves on the click, not a round trip later, and
  // snaps back if the PUT is refused
  async function toggle(on) {
    setS((p) => ({ ...p, enabled: on }))
    try { setS(await api('/api/reviewer', {
      method: 'PUT', body: JSON.stringify({ enabled: on }) })) }
    catch (e) { setS((p) => ({ ...p, enabled: !on })); notifyError(e) }
  }

  const recent = s.recent_auto || []
  return (
    <section className="sq-rail-sec" aria-label="Auto review">
      <h3 className="sq-rail-h">
        Auto review
        {/* the switch sits against the heading it names */}
        <Toggle checked={!!s.enabled} onChange={toggle} label="Auto review"
                onText="On" offText="Off"
                title={s.enabled ? AUTO_REVIEW_ON_TIP : AUTO_REVIEW_OFF_TIP} />
      </h3>
      {AUTO_REVIEW_LEDE.map((t) => <p key={t}>{t}</p>)}

      {/* what waits on it, and what it handed to you: an item it flagged is
          reviewed but not clear, it waits on the operator in the queue */}
      {(untriaged > 0 || flagged > 0) && (
        <div className="sq-states">
          {flagged > 0 && <State tone="warn">{flagged} flagged for you in the queue</State>}
          {untriaged > 0 && <State>{untriaged} not reviewed yet</State>}
        </div>
      )}
      {(untriaged > 0 || s.running) && (
        <Button variant="ghost" className="sq-more-btn" disabled={busy || s.running}
                title={`run Auto review over the ${untriaged} item(s) not reviewed yet, now`}
                onClick={() => act('/api/reviewer/run')}>
          {s.running ? 'Running…' : 'Review now'}
        </Button>
      )}

      {last && (
        <p>Last run {ts(last.finished_at || last.started_at)}: {tallyLine(last)}.</p>
      )}

      {recent.length > 0 && (
        <>
          <button className="sq-fold" type="button" aria-expanded={logOpen}
                  onClick={() => setLogOpen((o) => !o)}>
            {logOpen ? 'Hide' : 'Show'} what it handled recently ({recent.length})
          </button>
          {logOpen && (
            <ul className="sq-auto-list">
              {recent.map((l) => (
                <li key={`l${l.id}`}>
                  <div>
                    <span className="mono" title={l.subject}>{l.subject}</span>
                    <span className="sq-why">
                      {l.action === 'approved' ? 'Allowed' : 'Cleared'}
                      {l.project_slug ? ` for ${l.project_slug}` : ''}, {ts(l.created_at)}: {l.reason}
                    </span>
                  </div>
                  <Button variant="ghost" title="undo this auto-action" disabled={busy}
                          onClick={() => act(`/api/reviewer/log/${l.id}/undo`)}>Undo</Button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  )
}
