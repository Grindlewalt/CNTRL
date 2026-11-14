import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, subscribeSse } from '../api.js'
import { notifyError } from '../notify.js'
import { useAsk } from '../ask.jsx'
import { HostRequest, useEgressDecide } from '../EgressDecide.jsx'
import { AUTO_ALLOW_LABEL, AUTO_ALLOW_LEDE, WAITING_LEDE } from '../securityCopy.js'
import { human, tsShort } from '../format.js'
import { Link } from 'react-router-dom'
import { updateProfile } from '../boxes/api/profiles.js'
import { Button, HelpTip, Input, Select, SidePanel, Toggle } from '../components/index.js'
import { Group, Section, State } from '../securityParts.jsx'
import { listProfiles } from '../boxes/api/profiles.js'
import RunsIn from '../boxes/RunsIn.jsx'
import {
  allowHost, allowlist, getLan, promoteAuto, putLan, revokeAllow,
} from '../boxes/api/policy.js'
import {
  GENERAL, IMAGE_BUILD, needsProject, newSitesText, parseHosts, profilePayload, projectLabel,
} from '../boxes/logic.js'

// The guest's network, read like a log: what is waiting on you, what was
// decided (and by whom — you, the allowlist, or the auto guesser), and what
// is standing on the allowlist, each entry revocable where it sits.
//
// Every string here — host, path, project, reason — is UNTRUSTED (it is guest
// traffic, or a model's one-line guess about it). It is only ever rendered as
// plain text nodes, never markup.

const FEED_CAP = 300

// egress_events.verdict -> the state word a row says. Three words: allowed,
// blocked, cut. Colour is for what wants a look: a block is amber, a cut red,
// an approval green; plain allowed traffic is quiet. An auto decision says
// "auto" after the word; a queue approval (logged so a block isn't the last
// word on a host since let through) says who approved it.
const CUT_TIP = 'Cut by the anomaly guard: an allowed site looked like data leaving '
  + '(a random-looking name, a traffic spike, or clockwork-regular connections). '
  + 'The proxy stopped it there and refuses every later request to that site.'
const VERDICTS = {
  allow: { text: 'Allowed', tone: 'quiet' },
  deny: { text: 'Blocked', tone: 'warn' },
  auto_allow: { text: 'Allowed', tone: 'quiet', auto: true },
  auto_deny: { text: 'Blocked', tone: 'warn', auto: true },
  cut: { text: 'Cut', tone: 'bad', tip: CUT_TIP },
  approved: { text: 'Approved', tone: 'ok', note: 'approved by you' },
  reviewer_approved: { text: 'Approved', tone: 'ok', auto: true, note: 'by Auto review' },
}
const APPROVALS = new Set(['approved', 'reviewer_approved'])
const verdictOf = (v) => VERDICTS[v] || { text: v || '?', tone: 'quiet' }

function Verdict({ verdict }) {
  const v = verdictOf(verdict)
  return (
    <span className="nw-verdict" title={v.tip || (v.auto ? 'decided automatically' : v.note) || undefined}>
      <State tone={v.tone}>{v.text}</State>{v.auto && <i> auto</i>}
    </span>
  )
}

const projLabel = (slug, names) => projectLabel(slug, names)

// ---- data hooks -----------------------------------------------------------------

// The live egress feed. Seed from REST, then follow the stream. `project`
// scopes it: the panel wants only its own project's rows, so it filters at the
// source; the page holds everything and filters at render time, so switching
// its project picker never drops the socket.
function useEgressFeed(project, onEvent) {
  const [feed, setFeed] = useState([])
  const keyRef = useRef(0)
  const cb = useRef(onEvent)
  cb.current = onEvent
  useEffect(() => {
    let live = true
    api('/api/egress/events?limit=200').then((r) => {
      const evs = (Array.isArray(r) ? r : r.events) || []
      // REST rows carry project_slug; the live stream carries project
      const rows = evs.map((e) => ({ ...e, project: e.project ?? e.project_slug }))
      const kept = project ? rows.filter((e) => e.project === project) : rows
      if (live) setFeed(kept.map((e) => ({ ...e, _k: ++keyRef.current })))
    }).catch(() => {})
    const stop = subscribeSse('/api/egress/stream', (ev) => {
      if (ev.type !== 'egress') return
      if (project && ev.project !== project) return
      // stamped on arrival (UTC, like the DB's) so the row has a time at all
      const row = { ...ev, created_at: new Date().toISOString(), _k: ++keyRef.current }
      setFeed((f) => [row, ...f].slice(0, FEED_CAP))
      cb.current?.(ev)
    })
    return () => { live = false; stop() }
  }, [project])
  return feed
}

// Poll a GET every 10s (and on demand). Returns [data, reload].
function usePoll(path, pick) {
  const [data, setData] = useState(null)
  const reload = useCallback(() => {
    if (!path) return
    api(path).then((r) => setData(pick(r))).catch(() => {})
  }, [path]) // eslint-disable-line
  useEffect(() => {
    setData(null)
    reload()
    const t = setInterval(reload, 10000)
    return () => clearInterval(t)
  }, [reload])
  return [data, reload]
}

const q = (project) => (project ? `?project=${encodeURIComponent(project)}` : '')

// ---- the top: project, auto-allow, counts -----------------------------------------

function AutoToggle({ project, onChange }) {
  const [mode, reload] = usePoll(`/api/egress/auto${q(project)}`, (r) => r)
  async function flip(on) {
    try {
      await api('/api/egress/auto', {
        method: 'PUT',
        body: JSON.stringify({ project: project || null, mode: on ? 'on' : 'off' }) })
      reload(); onChange?.()
    } catch (err) { notifyError(err) }
  }
  const scope = !project ? 'This is the default for every project.'
    : mode && mode.project === null ? `This project follows the default (${mode.global}).`
      : 'Set for this project only.'
  return (
    <div className="nw-auto">
      <Toggle checked={!!mode?.effective} disabled={!mode} label={AUTO_ALLOW_LABEL}
              onText={AUTO_ALLOW_LABEL} offText={AUTO_ALLOW_LABEL} onChange={flip} />
      <p>{AUTO_ALLOW_LEDE} {scope}</p>
    </div>
  )
}

function Counts({ project, tick }) {
  const [c, reload] = usePoll(`/api/egress/summary${q(project)}`, (r) => r)
  useEffect(() => { reload() }, [tick]) // eslint-disable-line
  const n = (k) => (c ? c[k] : '–')
  return (
    <dl className="nw-counts"
        title="Sites, not requests. Allowed and blocked cover the last 24 hours; waiting is right now.">
      <div><dt>Allowed</dt><dd>{n('allowed')}</dd><small>sites, last 24 h</small></div>
      <div><dt>Blocked</dt><dd>{n('denied')}</dd><small>sites, last 24 h</small></div>
      <div className={c?.waiting ? 'warn' : undefined}>
        <dt>Waiting</dt><dd>{n('waiting')}</dd><small>for you now</small></div>
    </dl>
  )
}

// ---- waiting for you --------------------------------------------------------------

// Hosts the guest's code tried to reach that nobody has decided on. Allow
// always trains the allowlist up (the project's own list); Allow 1 h lets it
// through for an hour on no list; Deny keeps it out. WAITING_LEDE says so on
// the page, and useEgressDecide (shared with the Queue) does the asking.
function Waiting({ project, names, lastTry, tick, onDecided }) {
  const [pending, reload] = usePoll(`/api/egress/pending${q(project)}`, (r) => r.pending || [])
  useEffect(() => { reload() }, [tick]) // eslint-disable-line
  const { decide, picker } = useEgressDecide(() => { reload(); onDecided?.() }, { project, names })
  const rows = pending || []
  return (
    <Section title="Waiting for you" count={rows.length} lede={WAITING_LEDE}>
      {pending && rows.length === 0 && (
        <p className="sq-lede">Nothing is waiting. A site no list covers shows up here when a box
          tries to reach it.</p>)}
      {rows.length > 0 && (
        <Group>
          {rows.map((p) => {
            const last = lastTry(p.project_slug, p.host)
            const tried = last?.method ? `${last.method}${last.path ? ` ${last.path}` : ''}` : ''
            return (
              <HostRequest key={p.id} p={p} onDecide={decide} scoped={!!project}
                           projectName={projLabel(p.project_slug, names)} tried={tried} />
            )
          })}
        </Group>
      )}
      {picker}
    </Section>
  )
}

// ---- recent decisions ---------------------------------------------------------------

// A burst of identical requests (pip opening eight connections to one host)
// is ONE decision: consecutive rows with the same host, verdict and project
// fold into one line with a count and summed bytes.
function fold(feed) {
  const out = []
  for (const e of feed) {
    const prev = out[out.length - 1]
    if (prev && prev.host === e.host && prev.verdict === e.verdict
        && prev.project === e.project && (prev.box_id || null) === (e.box_id || null)) {
      prev.n += 1
      prev.bytes_out += Number(e.bytes_out) || 0
      prev.bytes_in += Number(e.bytes_in) || 0
      continue
    }
    out.push({ ...e, n: 1, bytes_out: Number(e.bytes_out) || 0,
               bytes_in: Number(e.bytes_in) || 0 })
  }
  return out
}

const RECENT = 30

// who made a decision, from its verdict (the server says the same on the All page)
const DECIDED_BY = {
  allow: "the profile's rules", deny: "the profile's rules", auto_allow: 'Auto allow',
  auto_deny: "the profile's rules", cut: 'the anomaly guard',
  approved: 'you, from the waiting queue', reviewer_approved: 'Auto review',
}

// profile name for a project slug, from the profiles' own project lists; a
// project on none is on the default. Rows from the paged API carry it already.
function useProfileOf() {
  const [profs, setProfs] = useState([])
  useEffect(() => { listProfiles().then(setProfs).catch(() => {}) }, [])
  return useCallback((slug) => {
    const p = profs.find((x) => (x.projects || []).includes(slug)) || profs.find((x) => x.is_default)
    return p ? p.name : ''
  }, [profs])
}

// One decision in full: when, what, who, which profile and project, why.
function DecisionFacts({ d, names, profile }) {
  const v = verdictOf(d.verdict)
  const req = d.method ? `${d.method}${d.path ? ` ${d.path}` : ''}` : ''
  const from = [d.box_id && `box ${d.box_id}`, d.service_id && `service #${d.service_id}`].filter(Boolean).join(', ')
  const rows = [
    ['When', d.created_at ? String(d.created_at).replace('T', ' ').slice(0, 19) : 'just now'],
    ['Decision', `${v.text}${v.auto ? ' (automatic)' : ''}`],
    ['Decided by', d.decided_by || DECIDED_BY[d.verdict] || d.verdict],
    ['Profile', profile || 'unknown'],
    ['Project', projLabel(d.project, names)],
    ['Site', d.host, true],
    req && ['Request', req, true],
    from && ['From', from, true],
    d.reason && ['Why', d.reason],
    d.n > 1 && ['Repeats', `${d.n} requests folded into this row`],
  ].filter(Boolean)
  return (
    <>
      <dl className="nw-facts">
        {rows.map(([k, val, mono]) => (
          <div key={k}><dt>{k}</dt><dd className={mono ? 'mono' : ''}>{val}</dd></div>))}
      </dl>
      {d.verdict === 'cut' && <div className="sq-sub">{CUT_TIP}</div>}
    </>
  )
}

function DecisionRow({ d, names, profile, onOpen, active }) {
  const v = verdictOf(d.verdict)
  return (
    <div className={`nw-dec${active ? ' open' : ''}`}>
      <button type="button" className="nw-dec-row" aria-expanded={active}
              title={d.reason || ''} onClick={() => onOpen(d)}>
        <span className="nw-dec-time">{tsShort(d.created_at) || 'now'}</span>
        <Verdict verdict={d.verdict} />
        <span className="nw-dec-host mono">{d.host}{d.n > 1 && (
          <span className="nw-n"> ×{d.n}</span>)}</span>
        <span className="nw-dec-proj" title={projLabel(d.project, names)}>{profile}</span>
        <span className="nw-dec-bytes mono" title={APPROVALS.has(d.verdict) ? undefined : 'sent / received'}>
          {APPROVALS.has(d.verdict) ? v.note
            : <>↑{human(d.bytes_out)} ↓{human(d.bytes_in)}</>}</span>
      </button>
    </div>
  )
}

// The detail drawer, shared by the short list and the All page.
function DecisionDrawer({ d, names, profile, onClose, onChanged }) {
  const { choose, picker } = useEgressDecide()
  async function allowAnyway() {
    try {
      const proj = needsProject(d) ? await choose(d.host) : d.project
      if (!proj) return
      await allowHost(proj, d.host)
      onChanged?.()
      onClose()
    } catch (err) { notifyError(err) }
  }
  return (
    <SidePanel open={!!d} onClose={onClose} title={d ? d.host : 'Decision'} width={440}>
      {d && <DecisionFacts d={d} names={names} profile={profile} />}
      {d && d.verdict === 'auto_deny' && d.project !== IMAGE_BUILD && (
        <Button variant="ghost" onClick={allowAnyway}>Allow it anyway</Button>)}
      {picker}
    </SidePanel>
  )
}

function Decisions({ feed, names, onChanged }) {
  const rows = useMemo(() => fold(feed).slice(0, RECENT), [feed])
  const profileOf = useProfileOf()
  const [open, setOpen] = useState(null)
  return (
    <Section title="Recent decisions" count={rows.length || null}
             actions={<Link className="ghost-link" to="/security/network/all">See all decisions</Link>}>
      {rows.length === 0 && (
        <p className="sq-lede">No traffic yet. Requests from the boxes appear here as they happen.</p>
      )}
      {rows.length > 0 && (
        <Group className="nw-dec-list">
          {rows.map((d) => (
            <DecisionRow key={d._k} d={d} names={names} profile={profileOf(d.project)}
                         active={open?._k === d._k} onOpen={setOpen} />))}
        </Group>
      )}
      {open && <DecisionDrawer d={open} names={names} profile={profileOf(open.project)}
                               onClose={() => setOpen(null)} onChanged={onChanged} />}
    </Section>
  )
}

// ---- all decisions: a page of its own, with search and filters ---------------------------

const PAGE = 50

export function NetworkAll() {
  const [q, setQ] = useState('')
  const [verdict, setVerdict] = useState('')
  const [profile, setProfile] = useState('')
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [profiles, setProfiles] = useState([])
  const [names, setNames] = useState({})
  const [open, setOpen] = useState(null)
  useEffect(() => {
    listProfiles().then(setProfiles).catch(() => {})
    api('/api/projects').then((r) => setNames(Object.fromEntries((r.projects || []).map((p) => [p.slug, p.name])))).catch(() => {})
  }, [])
  useEffect(() => {      // waits for a pause in typing
    const t = setTimeout(() => {
      const qs = new URLSearchParams({ limit: PAGE, offset })
      if (q.trim()) qs.set('q', q.trim())
      if (verdict) qs.set('verdict', verdict)
      if (profile) qs.set('profile', profile)
      api(`/api/egress/decisions?${qs}`).then(setData).catch((e) => { setData({ total: 0, rows: [] }); notifyError(e) })
    }, 200)
    return () => clearTimeout(t)
  }, [q, verdict, profile, offset])
  const reset = (fn) => (e) => { fn(e.target.value); setOffset(0) }
  const rows = data?.rows || []
  const total = data?.total || 0
  return (
    <div className="nw-view nw-all">
      <div className="nw-all-head">
        <Link className="ghost-link" to="/security/network">Back to Network</Link>
        <h3>All decisions</h3>
        <span className="sec-count">{data ? total : ''}</span>
      </div>
      <div className="nw-filters">
        <Input aria-label="Search decisions" type="search" placeholder="Search site, project or reason"
               value={q} onChange={reset(setQ)} />
        <Select aria-label="Decision" value={verdict} onChange={reset(setVerdict)} recentKey={false}
                options={[{ value: '', label: 'Any decision' }, { value: 'allowed', label: 'Allowed' },
                  { value: 'blocked', label: 'Blocked or cut' }, { value: 'approved', label: 'Approved by you' },
                  { value: 'auto_allow', label: 'Auto allowed' }, { value: 'cut', label: 'Cut' }]} />
        <Select aria-label="Profile" value={profile} onChange={reset(setProfile)} recentKey={false}
                options={[{ value: '', label: 'Any profile' },
                  ...profiles.map((p) => ({ value: String(p.id), label: p.name }))]} />
      </div>
      {data && rows.length === 0 && <p className="sq-lede">No decisions match.</p>}
      {rows.length > 0 && (
        <Group className="nw-dec-list">
          {rows.map((r) => {
            const d = { ...r, project: r.project_slug, n: 1 }
            return <DecisionRow key={r.id} d={d} names={names} profile={r.profile || ''}
                                active={open?.id === r.id} onOpen={setOpen} />
          })}
        </Group>
      )}
      <div className="nw-pager">
        <Button variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Newer</Button>
        <span className="dim small">{total ? `${offset + 1} to ${Math.min(offset + PAGE, total)} of ${total}` : ''}</span>
        <Button variant="ghost" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Older</Button>
      </div>
      {open && <DecisionDrawer d={open} names={names} profile={open.profile} onClose={() => setOpen(null)} />}
    </div>
  )
}

// ---- always allow / always block, one view per profile -----------------------
// The lists belong to the profile: every project on it shares them, and an
// approval from the waiting queue adds to the project's profile. Live auto
// allows (one hour) are per project and show under the profile they belong to.

// backend/egress.py decide(), in one sentence
const ORDER = 'A cut site, or a profile with the network off, is always blocked. Otherwise '
  + 'any "always block" entry wins; then any "always allow" entry (or a live auto allow) lets '
  + "it through; any other site follows the profile's new-sites rule."

// The sites CNTRL ships on the Default profile (package registries, GitHub)
// fold to one line until asked for
function BuiltIns({ entries, render }) {
  const [open, setOpen] = useState(false)
  if (!entries.length) return null
  return (
    <>
      <div className="nw-none">
        {entries.length} built-in site{entries.length === 1 ? '' : 's'} (package registries and code hosts).{' '}
        <button type="button" className="sq-fold" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? 'Hide' : 'Show'}</button>
      </div>
      {open && entries.map(render)}
    </>
  )
}

const WHO = { seed: 'built in', reviewer: 'added by Auto review', operator: '' }

function PolicyLists({ tick, onChanged }) {
  const ask = useAsk()
  const [groups, setGroups] = useState(null)
  const [profiles, setProfiles] = useState(null)
  const reload = useCallback(() => {
    allowlist().then(setGroups).catch(() => {})
    listProfiles().then(setProfiles).catch(() => setProfiles([]))
  }, [])
  useEffect(() => {
    const t = setInterval(reload, 10000)
    return () => clearInterval(t)
  }, [reload])
  useEffect(() => { reload() }, [tick, reload])
  const refresh = () => { reload(); onChanged?.() }

  const rows = useMemo(() => (profiles || []).map((p) => {
    const g = (groups || []).find((x) => x.kind === 'profile' && x.profile?.id === p.id)
    const entries = g?.entries || (p.allow_hosts || []).map((host) => ({ host, source: 'operator' }))
    const auto = (groups || []).filter((x) => x.kind === 'project' && (p.projects || []).includes(x.project))
      .flatMap((x) => (x.entries || []).filter((e) => e.source === 'auto').map((e) => ({ ...e, project: x.project })))
    const deny = new Set(p.deny_hosts || [])
    return { p, allow: entries.filter((e) => e.source !== 'auto').map((e) => ({ ...e, blocked: deny.has(e.host) })),
      auto, block: [...deny].sort() }
  }), [groups, profiles])

  async function remove(r, host, list) {
    const ok = await ask.confirm(`Remove ${host}?`, {
      body: list === 'deny'
        ? `It comes off the ${r.p.name} profile's always-block list; it is judged by what is left.`
        : `It comes off the ${r.p.name} profile's always-allow list; the next attempt is judged by what is left.`,
      confirmLabel: 'Remove', danger: true })
    if (!ok) return
    try { await revokeAllow({ project: `profile:${r.p.id}`, host, list }); refresh() } catch (err) { notifyError(err) }
  }
  async function add(r, list) {
    const host = await ask.prompt(list === 'deny' ? `Always block a site on ${r.p.name}` : `Always allow a site on ${r.p.name}`,
      '', { confirmLabel: list === 'deny' ? 'Block' : 'Allow' })
    if (!host) return
    const key = list === 'deny' ? 'deny_hosts' : 'allow_hosts'
    try {
      await updateProfile(r.p.id, profilePayload({ ...r.p, [key]: [...new Set([...(r.p[key] || []), ...parseHosts(host)])] }))
      refresh()
    } catch (err) { notifyError(err) }
  }
  async function keep(e) { try { await promoteAuto(e.id); refresh() } catch (err) { notifyError(err) } }

  const allowRow = (r, e) => (
    <div key={`a:${e.host}`} className={`nw-entry${e.blocked ? ' overruled' : ''}`}>
      <span className="mono" title={e.host}>{e.host}</span>
      <span className="nw-from">
        {WHO[e.source] || ''}{e.blocked ? `${WHO[e.source] ? ', ' : ''}overruled by a block` : ''}
      </span>
      <span className="sq-row-actions">
        {e.source !== 'seed' && (
          <Button variant="ghost" danger onClick={() => remove(r, e.host, 'allow')}>Remove</Button>)}
      </span>
    </div>
  )
  return (
    <Section title="Always allow and always block"
             actions={<>
               <HelpTip label="How a site is judged" align="right">{ORDER}</HelpTip>
               <Link className="ghost-link" to="/security/profiles">Edit profiles</Link></>}>
      {profiles && rows.length === 0 && <p className="sq-lede">No profiles yet.</p>}
      {rows.map((r) => (
        <Group key={r.p.id}>
          <div className="nw-proj-head">
            <b>{r.p.name}</b>
            <span className="sq-sub">
              {r.p.network_off ? 'Network off' : `New sites: ${newSitesText(r.p)}`}
              {(r.p.projects || []).length ? `, used by ${(r.p.projects || []).join(', ')}` : ''}
            </span>
            {!r.p.network_off && <>
              <Button variant="link" onClick={() => add(r, 'allow')}>Allow a site</Button>
              <Button variant="link" onClick={() => add(r, 'deny')}>Block a site</Button></>}
          </div>
          {r.p.network_off ? <div className="nw-none">No site gets through while the network is off.</div> : <>
            {r.allow.length === 0 && r.block.length === 0 && r.auto.length === 0 && (
              <div className="nw-none">Nothing listed: every site follows the new-sites rule.</div>)}
            {r.block.map((h) => (
              <div key={`d:${h}`} className="nw-entry">
                <span className="mono" title={h}>{h}</span>
                <span className="nw-from"><State tone="warn">Always blocked</State></span>
                <span className="sq-row-actions">
                  <Button variant="ghost" onClick={() => remove(r, h, 'deny')}>Remove</Button></span>
              </div>))}
            {r.auto.map((e) => (
              <div key={`u:${e.id}`} className="nw-entry">
                <span className="mono" title={e.host}>{e.host}</span>
                <span className="nw-from" title={e.reason || undefined}>
                  {e.rule === 'once' ? 'allowed for 1 h' : 'auto allow'} on {e.project}, until {tsShort(e.expires_at)}</span>
                <span className="sq-row-actions">
                  <Button variant="ghost" title="keep it on the profile's always-allow list"
                          onClick={() => keep(e)}>Keep</Button>
                  <Button variant="ghost" danger onClick={async () => {
                    try { await revokeAllow({ project: e.project, host: e.host, id: e.id }); refresh() }
                    catch (err) { notifyError(err) } }}>Revoke</Button></span>
              </div>))}
            {r.allow.filter((e) => e.source !== 'seed').map((e) => allowRow(r, e))}
            <BuiltIns entries={r.allow.filter((e) => e.source === 'seed')} render={(e) => allowRow(r, e)} />
          </>}
        </Group>
      ))}
    </Section>
  )
}

// ---- per-project LAN access -------------------------------------------------
// Box -> LAN only, through the host proxy: the guest still has no route to the
// LAN. Entries are RFC1918 CIDRs, addresses or names, optionally :port. The
// CNTRL host, loopback, link-local/metadata and the box network are refused at
// save and again at connect, even inside a listed range.
function LanAccess({ slug }) {
  const [lan, setLan] = useState(null)
  const [text, setText] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  function load() {
    getLan(slug).then((l) => { setLan(l); setText(l.allow.join('\n')) })
      .catch(() => setLan(null))
  }
  useEffect(() => { load() }, [slug]) // eslint-disable-line

  async function send(change) {
    setBusy(true)
    try {
      await putLan(slug, change)
      setStatus('Saved'); setTimeout(() => setStatus(''), 1500); load()
    } catch (err) { notifyError(err) }
    setBusy(false)
  }

  if (!lan || slug === GENERAL || slug === IMAGE_BUILD) return null
  return (
    <div className="nw-card">
      <h4>LAN access <span>{status}</span></h4>
      <Toggle checked={lan.enabled} disabled={busy} label="LAN access"
              onText="On: this project's boxes may reach the devices listed below"
              offText="Off: boxes in this project cannot reach any private or LAN address"
              onChange={(on) => send({ enabled: on, allow: parseHosts(text) })} />
      <Input textarea label="Allowed LAN targets (one per line: 10.0.0.0/24, 10.0.0.60:8123, nas.lan)"
             className="md-editor" rows={4} spellCheck={false} value={text}
             onChange={(e) => setText(e.target.value)} />
      <div className="nw-foot">
        <span>
          Never reachable, even inside a listed range: this server
          {lan.hostIps.length > 0 && <> (<span className="mono">{lan.hostIps.join(', ')}</span>)</>}, loopback,
          link-local and cloud metadata addresses, and the box network.
        </span>
        <Button disabled={busy} onClick={() => send({ allow: parseHosts(text) })}>
          {busy ? 'Saving…' : 'Save LAN list'}</Button>
      </div>
    </div>
  )
}

// ---- per-project secret grants ----------------------------------------------
function Grants({ slug }) {
  const [grants, setGrants] = useState([])
  const ask = useAsk()
  function load() {
    api(`/api/egress/grants/${slug}`).then((r) => setGrants(r.grants || [])).catch(() => setGrants([]))
  }
  useEffect(() => { load() }, [slug]) // eslint-disable-line

  async function set(secret, statusVal) {
    try {
      await api(`/api/egress/grants/${slug}`, {
        method: 'POST', body: JSON.stringify({ secret, status: statusVal }) })
      load()
    } catch (err) { notifyError(err) }
  }
  async function add() {
    const name = await ask.prompt('Secret name to grant to this project', '',
                                  { confirmLabel: 'Grant' })
    if (!name) return
    set(name.trim(), 'granted')
  }

  return (
    <div className="nw-card">
      <h4>Secret grants</h4>
      <div className="nw-foot">
        <span>Secrets this project may reference as <span className="mono">{'{{secret:NAME}}'}</span>.</span>
        <Button variant="ghost" onClick={add}>Grant a secret</Button>
      </div>
      {grants.length === 0 && <p>None granted.</p>}
      {grants.map((g) => {
        const granted = g.status === 'granted'
        return (
          <div key={g.secret_name} className="nw-entry">
            <span className="mono">{g.secret_name}</span>
            <span className="nw-from"><State tone={granted ? 'ok' : 'quiet'}>{granted ? 'Granted' : 'Revoked'}</State></span>
            <span className="sq-row-actions">
              <Button variant="ghost" danger={granted}
                      onClick={() => set(g.secret_name, granted ? 'revoked' : 'granted')}>
                {granted ? 'Revoke' : 'Grant'}</Button>
            </span>
          </div>
        )
      })}
    </div>
  )
}

// latest feed row per (project, host) — what the guest was doing when it asked
function useLastTry(feed) {
  const idx = useMemo(() => {
    const m = new Map()
    for (const e of feed) {
      const k = `${e.project || GENERAL}|${e.host}`
      if (!m.has(k) && e.method) m.set(k, e)
    }
    return m
  }, [feed])
  return useCallback((project, host) => idx.get(`${project || GENERAL}|${host}`), [idx])
}

// A tick that bumps on every decision the live feed reports (or one made here),
// so the counts, the queue and the allowlist refresh at once instead of on
// their next 10s poll.
// Coalesced: a guest retrying a denied host fires a burst, and each bump is
// three fetches.
function useTick() {
  const [tick, setTick] = useState(0)
  const timer = useRef(null)
  const bump = useCallback(() => {
    if (timer.current) return
    timer.current = setTimeout(() => { timer.current = null; setTick((t) => t + 1) }, 800)
  }, [])
  useEffect(() => () => clearTimeout(timer.current), [])
  return [tick, bump]
}
const decisive = (ev) => ev.verdict !== 'allow'

// Compact, project-scoped egress view for a Workspace panel: the same three
// sections as the page, for this one project, plus its policy and grants.
export function NetworkPanel({ slug }) {
  const [tick, bump] = useTick()
  const feed = useEgressFeed(slug, (ev) => decisive(ev) && bump())
  const lastTry = useLastTry(feed)
  return (
    <div className="pane-col nw-view nw-panel">
      <div className="nw-controls">
        <Counts project={slug} tick={tick} />
        <AutoToggle project={slug} onChange={bump} />
      </div>
      <Waiting project={slug} names={{}} lastTry={lastTry} tick={tick} onDecided={bump} />
      <Decisions feed={feed.slice(0, 60)} names={{}} onChanged={bump} />
      <PolicyLists tick={tick} onChanged={bump} />
      <div>
        <LanAccess slug={slug} />
        <RunsIn slug={slug} />
        <Grants slug={slug} />
      </div>
    </div>
  )
}


export default function Network() {
  const [projects, setProjects] = useState([])
  const [filter, setFilter] = useState('')      // '' = all projects
  const [tick, bump] = useTick()
  // the page holds every project's events and narrows at render, so changing
  // the filter never tears down the stream
  const feed = useEgressFeed('', (ev) => decisive(ev) && bump())
  const lastTry = useLastTry(feed)

  useEffect(() => {
    api('/api/projects').then((r) => setProjects(r.projects || [])).catch(() => {})
  }, [])
  const names = useMemo(
    () => Object.fromEntries(projects.map((p) => [p.slug, p.name])), [projects])

  const shown = filter ? feed.filter((e) => e.project === filter) : feed

  // No heading or page padding of its own: this renders as the Network tab of
  // the Security layout, which owns the title, the tab strip and the insets.
  return (
    <div className="nw-view">
      <div className="nw-top">
        <div className="nw-controls">
          <Select label="Project" value={filter} recentKey="project"
                  onChange={(e) => setFilter(e.target.value)}
                  options={[{ value: '', label: 'All projects' },
                    ...projects.map((p) => ({ value: p.slug, label: p.name }))]} />
          <AutoToggle project={filter} onChange={bump} />
        </div>
        <Counts project={filter} tick={tick} />
      </div>

      <Waiting project={filter} names={names} lastTry={lastTry} tick={tick} onDecided={bump} />
      <Decisions feed={shown} names={names} onChanged={bump} />
      <PolicyLists tick={tick} onChanged={bump} />

      {filter ? (
        <Section title={`${names[filter] || filter}: LAN access, box and secrets`}>
          <div>
            <LanAccess slug={filter} />
            <RunsIn slug={filter} />
            <Grants slug={filter} />
          </div>
        </Section>
      ) : (
        <p className="sq-lede">Choose a project at the top to edit its LAN access, the box it runs in
          and its secret grants.</p>
      )}
    </div>
  )
}
