import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from './api.js'
import { useAsk } from './ask.jsx'
import { human } from './format.js'
import { notify, notifyError } from './notify.js'
import { baselineAsk } from './securityCopy.js'
import { eventState } from './securityRuns.js'
import { Button } from './components/index.js'
import { State, Ticks } from './securityParts.jsx'

// The evidence board for one security alert. A card on the Security page says
// "write flag: network_call in fetch.py"; this is where the operator finds out
// whether that matters — the flagged lines in place, the diff against git HEAD,
// the directory the file lives in, the traffic behind an egress cut, the
// history behind a login burst. Assembled server-side by backend/secctx.py and
// shipped as TYPED SECTIONS (facts | code | diff | files | table | note), so a
// new alert kind gets a useful board with no change here.
//
// EVERYTHING on this board is UNTRUSTED: agent-written source, scanned
// hostnames, guest request paths, attempted usernames. It is rendered as plain
// text nodes only — no <Md>, no dangerouslySetInnerHTML, ever.

function Facts({ s }) {
  return (
    <dl className="bd-facts">
      {s.rows.map((r, i) => (
        <div key={i} className="bd-fact">
          <dt>{r.label}</dt>
          <dd>
            {r.value}
            {r.hint && <span className="bd-hint">{r.hint}</span>}
          </dd>
        </div>
      ))}
    </dl>
  )
}

// Line-numbered source with the flagged lines marked. The gutter is a separate
// column so selecting the code doesn't drag the numbers along with it.
function Code({ s }) {
  return (
    <div className="bd-code">
      {s.path && <div className="bd-code-path">{s.path}</div>}
      {s.blocks.map((b, i) => (
        <div key={i} className="bd-block">
          {i > 0 && <div className="bd-gap" aria-label="lines skipped">…</div>}
          {b.lines.map((ln) => (
            <div key={ln.n} className={ln.mark ? 'bd-line mark' : 'bd-line'}>
              <span className="bd-ln" aria-hidden="true">{ln.n}</span>
              <code>{ln.text || ' '}</code>
            </div>
          ))}
        </div>
      ))}
      {s.truncated && <div className="bd-trunc">The snippet is cut short here.</div>}
    </div>
  )
}

function Diff({ s }) {
  return (
    <div className="bd-code">
      {s.hunks.map((h, i) => (
        <div key={i} className="bd-block">
          <div className="bd-hunk">{h.header}</div>
          {h.lines.map((ln, j) => (
            <div key={j} className={`bd-line ${ln.k === '+' ? 'add' : ln.k === '-' ? 'del' : 'ctx'}`}>
              <span className="bd-ln" aria-hidden="true">{ln.k === ' ' ? '' : ln.k}</span>
              <code>{ln.text || ' '}</code>
            </div>
          ))}
        </div>
      ))}
      {s.truncated && <div className="bd-trunc">The diff is cut short here.</div>}
    </div>
  )
}

// The directory the flagged file sits in. `subject` is the file itself,
// `burst` means it was touched in the same write turn (the real blast radius),
// `flagged` means it carries an alert of its own.
function Files({ s }) {
  const box = useRef(null)
  const subject = useRef(null)
  // A real directory can be 60+ files, and the flagged one is wherever the
  // alphabet put it. The list scrolls inside its own box (so it never pushes
  // the diff off-screen) and opens parked on the subject — set directly rather
  // than via scrollIntoView, which would also scroll the modal body.
  useEffect(() => {
    if (box.current && subject.current) {
      box.current.scrollTop = Math.max(0, subject.current.offsetTop - 60)
    }
  }, [s])
  return (
    <ul className="bd-files" ref={box}>
      {s.entries.map((e) => (
        <li key={e.rel} className={e.subject ? 'subject' : ''}
            ref={e.subject ? subject : undefined}>
          <span className="mono">{e.name}{e.kind === 'dir' ? '/' : ''}</span>
          {e.subject && <span className="bd-word">this file</span>}
          {e.burst && <span className="bd-word">same write</span>}
          {e.flagged && <span className="bd-word">has its own alert</span>}
          <span className="bd-dim">
            {e.kind === 'dir'
              ? (e.count != null ? `${e.count} item${e.count === 1 ? '' : 's'}` : '')
              : (e.size == null ? '' : human(e.size))}
          </span>
          <span className="bd-dim">{e.ago}</span>
        </li>
      ))}
    </ul>
  )
}

// A table longer than a dozen rows shows the first dozen and folds the rest
// behind "show all N" (the related-alerts table can run to fifty).
const TABLE_ROWS = 12

function Table({ s }) {
  const [all, setAll] = useState(false)
  if (!s.rows.length) {
    return <div className="bd-note">{s.empty || 'Nothing recorded.'}</div>
  }
  const rows = all ? s.rows : s.rows.slice(0, TABLE_ROWS)
  return (
    <div className="bd-tablewrap">
      <table className="bd-table">
        <thead>
          <tr>{s.cols.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {s.rows.length > TABLE_ROWS && (
        <button type="button" className="sq-fold bd-more" onClick={() => setAll(!all)}>
          {all ? `Show the first ${TABLE_ROWS}` : `Show all ${s.rows.length}`}
        </button>
      )}
    </div>
  )
}

function Section({ s }) {
  const body = s.type === 'facts' ? <Facts s={s} />
    : s.type === 'code' ? <Code s={s} />
      : s.type === 'diff' ? <Diff s={s} />
        : s.type === 'files' ? <Files s={s} />
          : s.type === 'table' ? <Table s={s} />
            : <div className="bd-text">{s.text}</div>
  return (
    <section className="bd-sec">
      {s.title && <h4>{s.title}</h4>}
      {body}
      {s.note && <div className="bd-note">{s.note}</div>}
    </section>
  )
}

export default function SecurityBoard({ eventId, seed, onClose, onAck }) {
  const [board, setBoard] = useState(null)
  const [err, setErr] = useState(null)
  const [raw, setRaw] = useState(false)

  useEffect(() => {
    let live = true
    setBoard(null); setErr(null)
    api(`/api/security/events/${eventId}/context`)
      .then((b) => { if (live) setBoard(b) })
      .catch((e) => { if (live) setErr(e.detail || String(e)) })
    return () => { live = false }
  }, [eventId])

  // Escape closes. The scrim handles the click-away; this is the keyboard half.
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const ev = board?.event || seed || {}
  const st = eventState(ev)
  const when = (v) => String(v).replace('T', ' ').slice(0, 19)
  const ask = useAsk()
  const [busy, setBusy] = useState(false)

  // An unexpected-process alert can be allowed, not only acknowledged: the
  // program (in its unit), or everything its unit runs, joins the baseline so
  // it stops alerting. The server refuses what would hide too much.
  const proc = ev.kind === 'unexpected_process' && board?.detail?.exe ? board.detail : null
  const fault = ev.kind === 'harness_fault' ? (board?.detail || ev.detail || {}) : null
  async function allow(scope) {
    const q = baselineAsk(proc, scope)
    if (!await ask.confirm(q.title, { body: q.body, confirmLabel: q.confirm })) return
    setBusy(true)
    try {
      const r = await api(`/api/security/events/${ev.id}/baseline`, {
        method: 'POST', body: JSON.stringify({ scope }) })
      notify(`Allowed. ${r.acknowledged} alert${r.acknowledged === 1 ? '' : 's'} cleared.`,
             { life: 8 })
      window.dispatchEvent(new Event('jarvis-files-changed'))
      onClose()
    } catch (e) { notifyError(e) }
    setBusy(false)
  }

  return (
    <div className="bd-scrim" onClick={onClose}>
      <div className="bd-modal" onClick={(e) => e.stopPropagation()}
           role="dialog" aria-modal="true" aria-label="security alert detail">
        <div className="bd-head">
          <div className="bd-headtext">
            {ev.severity && <State tone={st.tone}>{st.word}</State>}
            <h2 className="bd-title"><Ticks text={board?.title || ev.summary || 'Loading…'} /></h2>
            <div className="bd-sub">
              {ev.kind && <span className="mono">{ev.kind}</span>}
              {ev.project_slug && <span>in {ev.project_slug}</span>}
              {ev.created_at && <span>{when(ev.created_at)}</span>}
              {/* repeats were counted onto this row; the evidence is the first */}
              {ev.count > 1 && (
                <span>seen {ev.count} times{ev.last_seen && `, last ${when(ev.last_seen)}`}</span>)}
              {ev.acknowledged ? <span>acknowledged</span> : null}
              {/* their own click, filed already acknowledged (Settings, Alerts) */}
              {ev.actor === 'operator' && <span>by you</span>}
              {ev.quiet === 'kind' && <span>recorded only</span>}
            </div>
          </div>
          <Button variant="ghost" className="bd-close" onClick={onClose} title="close (Esc)">Close</Button>
        </div>

        <div className="bd-body">
          {err && <div className="bd-err">Could not load the evidence: {err}</div>}
          {!board && !err && <div className="bd-brief"><p className="bd-note">Gathering the evidence…</p></div>}

          {board && (board.why || board.checks?.length > 0) && (
            <section className="bd-brief">
              {board.why && <p>{board.why}</p>}
              {board.checks?.length > 0 && (
                <ul>
                  {board.checks.map((c, i) => <li key={i}>{c}</li>)}
                </ul>
              )}
            </section>
          )}

          {board?.sections.map((s, i) => <Section key={i} s={s} />)}

          {board && (
            <section className="bd-sec">
              <button type="button" className="sq-fold" onClick={() => setRaw((r) => !r)}>
                {raw ? 'Hide' : 'Show'} the raw event
              </button>
              {raw && (
                <pre className="lg-pre bd-raw">
                  {JSON.stringify({ ...ev, detail: board.detail }, null, 2)}
                </pre>
              )}
            </section>
          )}
        </div>

        <div className="bd-foot">
          {ev.triage_verdict === 'flag' && (
            <State tone="warn" why={ev.triage_reason || undefined}>Flagged by Auto review</State>)}
          <span className="sq-spacer" />
          {proc && proc.unit && (
            <Button variant="ghost" disabled={busy} onClick={() => allow('unit')}
                    title={`Allow everything ${proc.unit} runs, in every box`}>
              Allow the whole unit</Button>
          )}
          {proc && (
            <Button variant="ghost" disabled={busy} onClick={() => allow('program')}
                    title="Stop alerting on this program in this unit, in every box">
              Allow this program</Button>
          )}
          {fault && fault.conversation_id && (
            <Link className="ghost-link" to={`/c/${fault.conversation_id}`} onClick={onClose}
                  title="open the chat where this happened">Open the chat</Link>
          )}
          {onAck && !ev.acknowledged && (
            <Button onClick={() => { onAck(ev.id); onClose() }}
                    title={fault ? 'You have dealt with it, or noted the bug. It leaves the list.'
                      : 'Mark it seen. A repeat raises a new alert.'}>
              {fault ? 'Mark resolved' : 'Acknowledge'}</Button>
          )}
        </div>
      </div>
    </div>
  )
}
