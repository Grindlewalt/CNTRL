import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import Md from '../Md.jsx'
import { ago, human, ts } from '../format.js'
import { notify, notifyError } from '../notify.js'
import { Button, Select, Tabs, Toggle } from '../components/index.js'
import { Group, Section } from '../securityParts.jsx'
import { mergeConvos } from '../logsList.js'
import { proseLabel, timelineHeader } from '../logsTimeline.js'

// Logs: full transcript viewer for any conversation — every user/assistant
// message and every tool call with its args and result — plus the numbers that
// explain a token blow-up (tool-call counts, result bytes, real token usage).
// A debugging / observability tool. Tool args and results are UNTRUSTED: they
// are always rendered as plain text in <pre>, never markdown / HTML. The text
// the agent wrote between its calls (`narration` items) is the agent's own
// prose, drawn like a message but marked as what it is.

const RESULT_HOT = 4000       // a single result this big is re-sent every iteration
const HEAVY_TOKENS = 500000   // runaway-conversation flags in the left rail
const HEAVY_CALLS = 30
// The list is a snapshot of the ledger, not a live feed: a slow answer must
// never be asked again before it lands (the 5 s poll used to stack requests
// behind a query that took longer than the interval).
const POLL_MS = 15000

// A conversation's summary is often the first line of a prompt: a markdown
// heading ("# AGENT BRIEF: …"), an agent run's "[agent-name] …" prefix, runs
// of whitespace. The title is the words; a leading [tag] moves to the caption.
function cleanTitle(summary, id) {
  let t = String(summary || '').replace(/\s+/g, ' ').trim()
  let tag = null
  const m = t.match(/^\[([^\]]{1,48})\]\s*/)
  if (m) { tag = m[1].trim(); t = t.slice(m[0].length) }
  t = t.replace(/^[#[\s]+/, '').trim()
  return { title: t || `conversation ${id}`, tag }
}

// The one line under a title, as a sentence: "agent-x in website, 3h ago"
function Caption({ tag, project, when }) {
  const where = [tag, project && `in ${project}`].filter(Boolean).join(' ')
  const text = [where, ago(when)].filter(Boolean).join(', ')
  return text ? <div className="lg-caption" title={ts(when)}>{text[0].toUpperCase() + text.slice(1)}</div> : null
}

// the expand mark of a row that opens
const Chev = () => (
  <svg className="lg-chev" viewBox="0 0 10 10" aria-hidden="true">
    <path d="M3 1.5 6.5 5 3 8.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
  </svg>
)

// token counts -> K / M
function tok(n) {
  const v = Number(n) || 0
  if (v < 1000) return `${v}`
  if (v < 1000000) return `${(v / 1000).toFixed(1)}K`
  return `${(v / 1000000).toFixed(2)}M`
}

function prettyArgs(args) {
  if (args == null) return ''
  if (typeof args === 'object') {
    try { return JSON.stringify(args, null, 2) } catch { return String(args) }
  }
  const s = String(args)
  try { return JSON.stringify(JSON.parse(s), null, 2) } catch { return s }
}

// one figure of a stat strip: the label, the number, an optional note
function Stat({ label, value, sub, bad }) {
  return (
    <div className={bad ? 'bad' : undefined}>
      <dt>{label}</dt>
      <dd>{value}{sub && <small>{sub}</small>}</dd>
    </div>
  )
}

// human() stops at MB; captured context and the database can be GB
const GB = 1024 ** 3
const sizeOf = (n) => (Number(n) >= GB ? `${(Number(n) / GB).toFixed(1)} GB` : human(n))
const KEEP_CHOICES = [1, 3, 7, 14, 30]
const dayLabel = (d) => `${d} day${d === 1 ? '' : 's'}`
const dayOptions = KEEP_CHOICES.map((d) => ({ value: d, label: dayLabel(d) }))

function usd(n) {
  const v = Number(n) || 0
  return v >= 0.01 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`
}

// One captured model call: token bill up front, raw context on demand.
// Context text is UNTRUSTED (it embeds tool results / web content) — always
// rendered inside <pre>, never through <Md>.
function CallItem({ call, index, prevInput }) {
  const [open, setOpen] = useState(false)
  const [ctx, setCtx] = useState(null)
  const [err, setErr] = useState(null)
  const delta = prevInput != null ? call.input_tokens - prevInput : null

  async function toggle() {
    if (open) { setOpen(false); return }
    setOpen(true)
    if (!ctx && !err && call.has_context) {
      try { setCtx(await api(`/api/logs/calls/${call.id}/context`)) }
      catch (e) { setErr(e.detail || String(e)) }
    }
  }
  return (
    <div className={`lg-tool${open ? ' open' : ''}`}>
      <button type="button" className="lg-tool-head" onClick={toggle} aria-expanded={open}>
        <Chev />
        <span className="lg-tool-name">Call {index + 1}</span>
        <span className="lg-tool-meta">
          {tok(call.input_tokens)} in
          {delta != null && delta !== 0 && ` (${delta > 0 ? '+' : ''}${tok(delta)})`}, {tok(call.output_tokens)} out,
          cache {tok(call.cache_hit)} hit and {tok(call.cache_miss)} miss
        </span>
        <span className="lg-when"><span className="mono">{usd(call.cost_usd)}</span> {ts(call.created_at)}</span>
      </button>
      {open && (
        <div className="lg-tool-body">
          {!call.has_context && (
            <p className="sq-change">No raw context is stored for this call:
              capture was switched off when it ran, or it is past the retention
              window. Both are set on the Cost tab.</p>
          )}
          {err && <p className="sq-change">{err}</p>}
          {ctx && (
            <>
              <p className="sq-change">
                {ctx.messages.length} messages and {ctx.n_tools} tool schemas were sent. The schemas
                count toward input tokens but are not shown.
              </p>
              {ctx.messages.map((m, i) => {
                const text = typeof m.content === 'string'
                  ? m.content : JSON.stringify(m.content)
                const body = m.tool_calls
                  ? `${text || ''}\n[tool_calls] ${JSON.stringify(m.tool_calls)}`
                  : text || ''
                return (
                  <div key={i}>
                    <div className="lg-label">
                      {i + 1}. {m.role}{m.name ? ` (${m.name})` : ''}, {human(body.length)}
                    </div>
                    <pre className="lg-pre">
                      {body.length > 20000
                        ? `${body.slice(0, 20000)}\n…(truncated for display)` : body}
                    </pre>
                  </div>
                )
              })}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// The cost card's setting and what it costs: capture is on unless switched off,
// so this says what is being kept, for how long, and how to shrink it. The
// delete asks inline (no confirm() dialog: it freezes the extension and is
// swallowed by iOS standalone).
function CapturePanel({ data, onToggle }) {
  const [st, setSt] = useState(null)      // /api/logs/storage
  const [ageDays, setAgeDays] = useState(1)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const loadSt = () => api('/api/logs/storage').then(setSt).catch(() => {})
  useEffect(() => { loadSt() }, [])
  const on = !!data.capture_context

  async function setKeep(days) {
    try {
      const r = await api('/api/logs/capture-retention', {
        method: 'POST', body: JSON.stringify({ days }) })
      if (r.deleted) notify(`Now keeping ${dayLabel(days)}: context of ${r.deleted} `
                            + `calls deleted, ${sizeOf(r.freed_bytes)} freed`, { life: 8 })
    } catch (e) { notifyError(e) }
    loadSt()
  }
  async function del() {
    setBusy(true)
    try {
      const r = await api('/api/logs/prune-context', {
        method: 'POST', body: JSON.stringify({ older_than_days: ageDays }) })
      notify(r.deleted
        ? `Deleted the context of ${r.deleted} calls, ${sizeOf(r.freed_bytes)} freed`
        : `Nothing captured is older than ${dayLabel(ageDays)}`, { life: 8 })
    } catch (e) { notifyError(e) }
    setBusy(false); setConfirming(false); loadSt()
  }

  return (
    <>
      <div className="lg-capture-row">
        <Toggle checked={on} label="Capture raw context"
                onText="Capture raw context" offText="Capture raw context"
                onChange={async (v) => { await onToggle(v); loadSt() }} />
        <span>
          {on ? 'Capturing every call' : 'Not capturing new calls'}
          {st && (st.captured_calls > 0
            ? `; ${sizeOf(st.captured_bytes)} kept for ${dayLabel(st.keep_days)}.`
            : '; nothing kept yet.')}
        </span>
      </div>
      {st?.over?.length > 0 && (
        <div className="lg-warn" role="status">{st.message}</div>
      )}
      {st && (confirming ? (
        <div className="lg-capture-row">
          <span className="sq-spacer">
            Delete captured context older than {dayLabel(ageDays)}? Token counts
            and costs stay; only the stored message arrays go.</span>
          <Button danger onClick={del} disabled={busy}>
            {busy ? 'Deleting…' : 'Delete'}</Button>
          <Button variant="ghost" onClick={() => setConfirming(false)} disabled={busy}>
            Cancel</Button>
        </div>
      ) : (
        <div className="lg-capture-row">
          <span>Keep for</span>
          <Select aria-label="Keep captured context for" value={st.keep_days}
                  options={dayOptions}
                  onChange={(e) => setKeep(Number(e.target.value))} />
          <span className="sq-spacer" />
          <span>Delete captured context older than</span>
          <Select aria-label="Delete captured context older than" value={ageDays}
                  options={dayOptions}
                  onChange={(e) => setAgeDays(Number(e.target.value))} />
          <Button variant="ghost" onClick={() => setConfirming(true)}
                  disabled={!st.captured_calls}>Delete…</Button>
        </div>
      ))}
    </>
  )
}

function CostView() {
  const [data, setData] = useState(null)
  const load = () => api('/api/logs/costs').then(setData).catch(() => {})
  useEffect(() => { load() }, [])
  if (!data) return <p className="lg-empty">Loading…</p>
  const p = data.prices_per_m
  const order = ['24h', '7d', '30d', 'all']
  const name = (w) => (w === 'all' ? 'All time' : `Last ${w.replace('h', ' h').replace('d', ' days')}`)
  return (
    <div className="lg-detail">
      <Group>
        {/* the tab strip already says "cost"; this is the one setting */}
        <div className="lg-capture">
          <CapturePanel data={data} onToggle={async (on) => {
            setData((d) => ({ ...d, capture_context: on }))
            try {
              await api('/api/logs/capture-context', {
                method: 'POST', body: JSON.stringify({ enabled: on }) })
            } catch { /* load() below restores the real state */ }
            load()
          }} />
        </div>
      </Group>
      <dl className="lg-stats">
        {order.map((w) => (
          <Stat key={w} label={name(w)} value={usd(data.windows[w]?.cost_usd)}
                sub={`${data.windows[w]?.calls || 0} calls`} />
        ))}
      </dl>
      {order.map((w) => {
        const d = data.windows[w]
        if (!d) return null
        return (
          <Section key={w} title={name(w)} count={`${d.calls} model calls`}>
            <Group>
              <div className="lg-bill">
                <span className="lg-k">Cache hit <span>{tok(d.cache_hit)} tokens at ${p.cache_hit} per million</span></span>
                <span className="mono">{usd(d.cache_hit * p.cache_hit / 1e6)}</span>
                <span className="lg-k">Cache miss <span>{tok(d.cache_miss)} tokens at ${p.cache_miss} per million</span></span>
                <span className="mono">{usd(d.cache_miss * p.cache_miss / 1e6)}</span>
                <span className="lg-k">Output <span>{tok(d.output)} tokens at ${p.output} per million</span></span>
                <span className="mono">{usd(d.output * p.output / 1e6)}</span>
                <span className="lg-k lg-total">Total</span>
                <span className="mono lg-total">{usd(d.cost_usd)}</span>
              </div>
            </Group>
          </Section>
        )
      })}
    </div>
  )
}

// The agent's own text between two tool calls (backend/narration.py): not a
// reply, not a result. Marked as such so it is never read as either.
function NarrationItem({ item }) {
  return (
    <div className="lg-msg narration">
      <div className="lg-msg-head">
        <b>{proseLabel(item)}</b>
        <span>between tool calls</span>
        {item.ts && <span className="lg-when">{ts(item.ts)}</span>}
      </div>
      <div className="lg-msg-body"><Md text={item.text} /></div>
    </div>
  )
}

function ToolItem({ item }) {
  const [open, setOpen] = useState(false)
  const hot = (item.result_bytes || 0) > RESULT_HOT
  const result = String(item.result ?? '')
  const truncated = result.length > 10000
  const shown = truncated ? `${result.slice(0, 10000)}\n…(truncated)` : result
  return (
    <div className={`lg-tool${open ? ' open' : ''}`}>
      <button type="button" className="lg-tool-head" onClick={() => setOpen((o) => !o)}
              aria-expanded={open}>
        <Chev />
        <span className="mono lg-tool-name">{item.tool}</span>
        <span className={`lg-tool-meta${hot ? ' hot' : ''}`}
              title={hot ? 'a result this big is sent again with every later model call' : undefined}>
          {human(item.result_bytes)} result{hot ? ', large' : ''}</span>
        <span className="lg-when">{ts(item.ts)}</span>
      </button>
      {open && (
        <div className="lg-tool-body">
          <div className="lg-label">Arguments</div>
          <pre className="lg-pre">{prettyArgs(item.args)}</pre>
          <div className="lg-label">Result</div>
          <pre className="lg-pre">{shown}</pre>
        </div>
      )}
    </div>
  )
}

export default function Logs() {
  const [convos, setConvos] = useState(null)   // null = first load in flight
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [calls, setCalls] = useState([])
  const [view, setView] = useState('logs')   // 'logs' | 'cost'
  const selectedRef = useRef(null)
  const inFlight = useRef(false)
  const shownRef = useRef(null)            // the list on screen, for the merge below
  const latestRef = useRef(null)           // the server's newest list, behind the pill
  const [fresh, setFresh] = useState(0)    // conversations that appeared since, not yet shown

  // one request at a time: a tick that finds the last one still out skips.
  // A hidden tab does not POLL, but its first load still runs (it used to stay
  // on "loading…" until the tab was shown and the next tick came round), and
  // becoming visible refreshes at once (WEB-22).
  const refresh = () => {
    if (inFlight.current || (document.hidden && shownRef.current !== null)) return
    inFlight.current = true
    api('/api/logs/conversations')
      .then((r) => {
        const m = mergeConvos(shownRef.current, r.conversations || [])
        latestRef.current = r.conversations || []
        shownRef.current = m.list
        setConvos(m.list); setFresh(m.newCount)
      })
      .catch(() => setConvos((c) => c ?? []))
      .finally(() => { inFlight.current = false })
  }
  // new conversations are counted behind a pill, not slid in under the pointer
  const showFresh = () => {
    shownRef.current = latestRef.current
    setConvos(latestRef.current); setFresh(0)
  }

  // only the transcripts view shows the list, so only it polls
  useEffect(() => {
    if (view !== 'logs') return undefined
    refresh()
    const t = setInterval(refresh, POLL_MS)
    const onShow = () => { if (!document.hidden) refresh() }
    document.addEventListener('visibilitychange', onShow)
    return () => { clearInterval(t); document.removeEventListener('visibilitychange', onShow) }
  }, [view]) // eslint-disable-line

  function open(id) {
    selectedRef.current = id
    setSelected(id); setDetail(null); setCalls([])
    api(`/api/logs/conversations/${id}`)
      .then((d) => { if (selectedRef.current === id) setDetail(d) })
      .catch(() => {})
    api(`/api/logs/conversations/${id}/calls`)
      .then((r) => { if (selectedRef.current === id) setCalls(r.calls) })
      .catch(() => {})
  }

  const stats = detail?.stats || {}
  const hist = detail?.tool_histogram || []
  const maxBytes = hist.reduce((m, h) => Math.max(m, h.bytes || 0), 0) || 1
  const cacheTotal = (stats.cache_hit || 0) + (stats.cache_miss || 0)
  const cachePct = cacheTotal ? Math.round((stats.cache_hit / cacheTotal) * 100) : null

  const totalCost = calls.reduce((s, c) => s + (c.cost_usd || 0), 0)
  // the list row carries the project and start time the transcript omits
  const row = (convos || []).find((c) => c.id === detail?.id)
  const head = detail ? cleanTitle(detail.summary, detail.id) : null

  // No heading of its own: this renders as the Logs tab of the Security layout.
  // The transcripts|cost switch sits above both views, and the conversation
  // list only exists in the transcripts view; it has nothing to say about cost.
  return (
    <div className="lg-view">
      <Tabs label="Logs view" value={view} onChange={setView}
            items={[{ id: 'logs', label: 'Transcripts', panel: 'logs-panel' },
                    { id: 'cost', label: 'Cost', panel: 'logs-panel' }]} />
      {view === 'cost' ? (
        <div className="lg-cost" id="logs-panel" role="tabpanel"><CostView /></div>
      ) : (
        <div className="lg-split" id="logs-panel" role="tabpanel">
          <aside className="lg-aside" aria-label="Conversations">
            {fresh > 0 && (
              <Button variant="ghost" className="lg-new" onClick={showFresh}>
                Show {fresh} new conversation{fresh === 1 ? '' : 's'}</Button>)}
            {convos === null && <p className="lg-empty">Loading…</p>}
            {convos?.length === 0 && (
              <p className="lg-empty">No conversations yet. Every chat and agent run is listed here
                once it starts.</p>)}
            <ul className="lg-list">
              {(convos || []).map((c) => {
                const { title, tag } = cleanTitle(c.summary, c.id)
                // a runaway conversation keeps a mark in the list; the
                // numbers behind it are in the opened transcript's header
                const heavy = (c.input_tokens || 0) > HEAVY_TOKENS
                  || (c.tool_calls || 0) > HEAVY_CALLS
                return (
                  <li key={c.id}>
                    <button type="button" className={`lg-row${selected === c.id ? ' active' : ''}`}
                            aria-current={selected === c.id ? 'true' : undefined}
                            onClick={() => open(c.id)}>
                      <span className="lg-row-top">
                        <span className="lg-title" title={title}>{title}</span>
                        {heavy && <span className="lg-heavy"
                                        title={`${c.tool_calls || 0} tool calls, ${tok(c.input_tokens)} input tokens`}>
                          Heavy</span>}
                      </span>
                      <Caption tag={tag} project={c.project} when={c.started_at} />
                    </button>
                  </li>
                )
              })}
            </ul>
          </aside>

          <main className="lg-pane">
            {!detail ? (
              <p className="lg-empty">{selected ? 'Loading…'
                : 'Choose a conversation to read its whole transcript: every message, every tool call with its arguments and result, and what each model call cost.'}</p>
            ) : (
              <div className="lg-detail">
                <header className="lg-head">
                  <h2 title={detail.summary || head.title}>{head.title}</h2>
                  <Caption tag={head.tag} project={row?.project} when={row?.started_at} />
                  <dl className="lg-stats">
                    <Stat label="Input tokens" value={tok(stats.input_tokens)}
                          bad={(stats.input_tokens || 0) > HEAVY_TOKENS} />
                    <Stat label="Output tokens" value={tok(stats.output_tokens)} />
                    <Stat label="Tool calls" value={stats.tool_calls || 0}
                          bad={(stats.tool_calls || 0) > HEAVY_CALLS} />
                    <Stat label="Result bytes" value={human(stats.result_bytes)} />
                    <Stat label="Model calls" value={stats.turns || 0} />
                    <Stat label="Cache hit" value={cachePct == null ? 'none' : `${cachePct}%`} />
                    {calls.length > 0 && <Stat label="Cost" value={usd(totalCost)} />}
                  </dl>
                </header>

                {calls.length > 0 && (
                  <Section title="Model calls" count={calls.length}
                           lede="The exact context sent with each call, when capture is on (set on the Cost tab).">
                    <div className="lg-timeline">
                      {calls.map((c, i) => (
                        <CallItem key={c.id} call={c} index={i}
                                  prevInput={i > 0 ? calls[i - 1].input_tokens : null} />
                      ))}
                    </div>
                  </Section>
                )}

                {hist.length > 0 && (
                  <Section title="Tools by result size" lede="Large results are sent again with every later call, so they drive the input tokens.">
                    <div className="lg-hist">
                      {hist.map((h) => (
                        <div key={h.tool} className="lg-hist-row">
                          <span className="mono" title={h.tool}>{h.tool}</span>
                          <span className="lg-n">{h.count} {h.count === 1 ? 'call' : 'calls'}</span>
                          <div className="lg-track">
                            <div className="lg-bar" style={{ width: `${((h.bytes || 0) / maxBytes) * 100}%` }} />
                          </div>
                          <span className="lg-b">{human(h.bytes)}</span>
                        </div>
                      ))}
                    </div>
                  </Section>
                )}
                <Section title="Transcript" lede={timelineHeader(detail.timeline || [])}>
                  <div className="lg-timeline">
                    {(detail.timeline || []).map((item, i) => (
                      item.kind === 'tool' ? (
                        <ToolItem key={i} item={item} />
                      ) : item.kind === 'narration' ? (
                        <NarrationItem key={i} item={item} />
                      ) : (
                        <div key={i} className={`lg-msg ${item.role}`}>
                          <div className="lg-msg-head">
                            <b>{proseLabel(item)}</b>
                            {item.ts && <span className="lg-when">{ts(item.ts)}</span>}
                          </div>
                          <div className="lg-msg-body"><Md text={item.content} /></div>
                        </div>
                      )
                    ))}
                    {(detail.timeline || []).length === 0 && (
                      <p className="lg-empty">This conversation has no messages yet.</p>
                    )}
                  </div>
                </Section>

              </div>
            )}
          </main>
        </div>
      )}
    </div>
  )
}
