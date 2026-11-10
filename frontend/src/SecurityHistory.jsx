import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from './api.js'
import SecurityBoard from './SecurityBoard.jsx'
import { ts, tsShort } from './format.js'
import { Tabs } from './components/index.js'
import { eventState } from './securityRuns.js'
import { Group, Section, State } from './securityParts.jsx'

// The Security log's recent past, acknowledged rows included: the one place a
// row filed already acknowledged ("by you", or a kind set to Record only) can be
// found, since it never reaches the queue or a toast. The newest dozen show; the
// rest are a click away.
// Every string is the server's or an agent's: plain text nodes only. Rows stay one
// per event here (the Queue groups them by run); a row names its chat when it has one.

const FILTERS = [
  ['all', 'Everything'],
  ['you', 'By you'],
  ['recorded', 'Recorded only'],
  ['rule', 'Filtered as normal work'],
]

// "Filtered as normal work": a rule judged the event routine (a scratch file the
// run made and threw away, a known device reconnecting, a process the agent's own
// run_code started). Recorded, acknowledged, never in the Queue; `rule` says why.
const keep = (filter) => (e) => (
  filter === 'you' ? e.actor === 'operator'
    : filter === 'recorded' ? e.quiet === 'kind'
      : filter === 'rule' ? e.quiet === 'rule'
        : true)

const FIRST = 12

export default function SecurityHistory() {
  const [rows, setRows] = useState(null)
  const [filter, setFilter] = useState('all')
  const [all, setAll] = useState(false)
  const [board, setBoard] = useState(null)

  useEffect(() => {
    let live = true
    api('/api/security/events?limit=200')
      .then((r) => { if (live) setRows(r.events || []) })
      .catch(() => { if (live) setRows([]) })
    return () => { live = false }
  }, [])

  const kept = (rows || []).filter(keep(filter))
  const shown = all ? kept : kept.slice(0, FIRST)
  return (
    <Section title="History" className="sq-history"
             lede="Every security event, acknowledged ones included. Open one to see its evidence.">
      <Tabs label="Filter the history" value={filter} onChange={(v) => { setFilter(v); setAll(false) }}
            items={FILTERS.map(([id, label]) => ({ id, label }))} />
      {rows === null && <p className="sq-lede">Loading…</p>}
      {rows !== null && kept.length === 0 && (
        <p className="sq-lede">{filter === 'all'
          ? 'No security events yet. They are recorded here as they happen.'
          : 'No events match this filter.'}</p>)}
      {shown.length > 0 && (
        <Group>
          {shown.map((e) => {
            const st = eventState(e)
            const when = e.count > 1 && e.last_seen ? e.last_seen : e.created_at
            return (
              <div key={e.id} className="sq-hist-row">
                <span className="sq-hist-time" title={ts(when)}>{tsShort(when)}</span>
                <div className="sq-hist-main">
                  <button type="button" className="sq-hist-open" onClick={() => setBoard(e)}
                          title="open the evidence">{e.summary}</button>
                  <span className="sq-hist-sub">
                    <span className="mono">{e.kind}</span>
                    {e.project_slug && <> in {e.project_slug}</>}
                    {e.conversation_id && <>, <Link to={`/c/${e.conversation_id}`}>chat {e.conversation_id}</Link></>}
                    {e.count > 1 && <>, {e.count} times</>}
                    {e.quiet === 'rule' && e.rule && <>. Normal work: {e.rule}</>}
                  </span>
                </div>
                <span className="sq-hist-word">
                  <State tone={st.tone}>{st.word}</State>
                  <span className="sq-sub">{e.actor === 'operator' ? 'by you'
                    : e.quiet === 'kind' ? 'recorded only'
                      : e.quiet === 'rule' ? 'normal work'
                        : !e.acknowledged ? 'waiting' : 'seen'}</span>
                </span>
              </div>
            )
          })}
        </Group>
      )}
      {kept.length > FIRST && (
        <button type="button" className="sq-fold" onClick={() => setAll(!all)}>
          {all ? 'Show fewer' : `Show all ${kept.length}`}</button>)}
      {board && <SecurityBoard eventId={board.id} seed={board} onClose={() => setBoard(null)} />}
    </Section>
  )
}
