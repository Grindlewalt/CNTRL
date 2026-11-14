import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, subscribeSse } from '../api.js'
import { notifyError } from '../notify.js'
import { useAsk } from '../ask.jsx'
import { HostRequest, useEgressDecide } from '../EgressDecide.jsx'
import { AUTO_ALLOW_LABEL, AUTO_ALLOW_LEDE, WAITING_LEDE } from '../securityCopy.js'
import { human, tsShort } from '../format.js'
import { Link } from 'react-router-dom'
import { Button, Input, Select, Toggle } from '../components/index.js'
import { Group, Section, State } from '../securityParts.jsx'
import { listProfiles } from '../boxes/api/profiles.js'
import RunsIn from '../boxes/RunsIn.jsx'
import {
  allowHost, allowlist, getLan, getPolicy, promoteAuto, promoteToProfile,
  putLan, putPolicy, revokeAllow,
} from '../boxes/api/policy.js'
import {
  GENERAL, IMAGE_BUILD, needsProject, newSitesText, parseHosts, POLICY_FROM, projectLabel,
  projectPolicy,
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

function DecisionRow({ d, names, onAllow }) {
  const [open, setOpen] = useState(false)
  const req = d.method ? `${d.method}${d.path ? ` ${d.path}` : ''}` : ''
  const v = verdictOf(d.verdict)
  return (
    <div className={`nw-dec${open ? ' open' : ''}`}>
      <button type="button" className="nw-dec-row" aria-expanded={open}
              title={d.reason || ''} onClick={() => setOpen(!open)}>
        <span className="nw-dec-time">{tsShort(d.created_at) || 'now'}</span>
        <Verdict verdict={d.verdict} />
        <span className="nw-dec-host mono">{d.host}{d.n > 1 && (
          <span className="nw-n"> ×{d.n}</span>)}</span>
        <span className="nw-dec-proj">{projLabel(d.project, names)}</span>
        <span className="nw-dec-bytes mono" title={APPROVALS.has(d.verdict) ? undefined : 'sent / received'}>
          {APPROVALS.has(d.verdict) ? v.note
            : <>↑{human(d.bytes_out)} ↓{human(d.bytes_in)}</>}</span>
      </button>
      {open && (
        <div className="nw-dec-detail">
          {d.reason && <div>{d.reason}</div>}
          {v.auto && <div className="sq-sub">Decided automatically{v.note ? `, ${v.note}` : ''}.</div>}
          {d.verdict === 'cut' && <div className="sq-sub">{CUT_TIP}</div>}
          {req && <div className="mono" title={req}>{req}</div>}
          {(d.box_id || d.service_id) && (
            <div className="sq-sub">
              {d.box_id && <>From box <span className="mono">{d.box_id}</span></>}
              {d.box_id && d.service_id ? ', ' : ''}
              {d.service_id ? `service #${d.service_id}` : ''}</div>)}
          {d.verdict === 'auto_deny' && d.project !== IMAGE_BUILD && (
            <Button variant="ghost" onClick={() => onAllow(d)}>Allow it anyway</Button>
          )}
        </div>
      )}
    </div>
  )
}

function Decisions({ feed, names, onChanged }) {
  const rows = useMemo(() => fold(feed), [feed])
  const { choose, picker } = useEgressDecide()
  async function allowAnyway(d) {
    try {
      const proj = needsProject(d) ? await choose(d.host) : d.project
      if (!proj) return
      await allowHost(proj, d.host)
      onChanged?.()
    } catch (err) { notifyError(err) }
  }
  return (
    <Section title="Recent decisions" count={rows.length || null}
             lede="Newest first. Repeats to the same site fold into one row; select a row to see why.">
      {rows.length === 0 && (
        <p className="sq-lede">No traffic yet. Requests from the boxes appear here as they happen.</p>
      )}
      {rows.length > 0 && (
        <Group className="nw-dec-list">
          {rows.map((d) => <DecisionRow key={d._k} d={d} names={names} onAllow={allowAnyway} />)}
        </Group>
      )}
      {picker}
    </Section>
  )
}

// ---- always allow / always block, one view per project -----------------------
// Each project with its profile and the profile's new-sites rule, then the
// lists that actually apply to it: the profile's and the project's own,
// merged, each entry marked with where it lives. Project entries are edited
// here (or in the project's own lists below); profile entries on the profile.
// Every approval writes the project's OWN list (DESIGN-BOXES (c)).

// backend/egress.py decide(), in one sentence
const ORDER = 'A cut site, or a profile with the network off, is always blocked. Otherwise '
  + 'any "always block" entry wins; then any "always allow" entry (or a live auto allow) lets '
  + "it through; any other site follows the profile's new-sites rule."

// The sites CNTRL ships on the general list (package registries, GitHub): the
// same dozen under every project, so they fold to one line until asked for
function BuiltIns({ entries, render }) {
  const [open, setOpen] = useState(false)
  if (!entries.length) return null
  return (
    <>
      <div className="nw-none">
        {entries.length} built-in site{entries.length === 1 ? '' : 's'} on the general list
        (package registries and code hosts).{' '}
        <button type="button" className="sq-fold" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? 'Hide' : 'Show'}</button>
      </div>
      {open && entries.map(render)}
    </>
  )
}

// allow-entry source (who put it there) -> a few words
const WHO = { seed: 'built in', reviewer: 'added by Auto review', operator: '' }

function PolicyLists({ project, names, projects, tick, onChanged }) {
  const ask = useAsk()
  const [groups, setGroups] = useState(null)
  const reload = useCallback(() => { allowlist().then(setGroups).catch(() => {}) }, [])
  useEffect(() => {
    const t = setInterval(reload, 10000)
    return () => clearInterval(t)
  }, [reload])
  const [profiles, setProfiles] = useState([])
  const loadProfiles = useCallback(() => {
    listProfiles().then(setProfiles).catch(() => setProfiles([]))
  }, [])
  useEffect(() => { reload(); loadProfiles() }, [tick]) // eslint-disable-line
  const rows = useMemo(() => projectPolicy({
    groups: groups || [], profiles, filter: project,
    projects: projects.length ? projects : Object.entries(names).map(([slug, name]) => ({ slug, name })),
  }), [groups, profiles, project, projects, names])
  const refresh = () => { reload(); loadProfiles(); onChanged?.() }

  // project and auto entries only: the revoke route keys on the project slug
  async function revoke(r, e, list = 'allow') {
    const ok = await ask.confirm(`Remove ${e.host}?`, {
      body: list === 'deny'
        ? `It comes off ${r.name}'s always-block list; it is judged by what is left.`
        : `It comes off ${r.name}'s always-allow list; the next attempt is judged by what is left.`,
      confirmLabel: 'Remove', danger: true })
    if (!ok) return
    try {
      await revokeAllow(e.from === 'auto' && e.id != null
        ? { project: r.slug, host: e.host, id: e.id }
        : { project: r.slug, host: e.host, list })
      refresh()
    } catch (err) { notifyError(err) }
  }
  async function keep(e) {
    try { await promoteAuto(e.id); refresh() } catch (err) { notifyError(err) }
  }
  async function addBlock(r) {
    const host = await ask.prompt(`Always block a site for ${r.name}`, '', { confirmLabel: 'Block' })
    if (!host) return
    try {
      const pol = await getPolicy(r.slug)
      await putPolicy(r.slug, undefined, [...new Set([...pol.project_deny, host.trim().toLowerCase()])])
      refresh()
    } catch (err) { notifyError(err) }
  }
  async function promote(r, host, list) {
    const prof = profiles.find((p) => p.id === r.profile.id) || r.profile
    const others = (prof.projects || []).filter((x) => x !== r.slug)
    const what = list === 'deny' ? 'always-block' : 'always-allow'
    const ok = await ask.confirm(`Move ${host} to the ${prof.name} profile's ${what} list?`, {
      body: `It leaves ${r.name}'s own list and applies to every project on ${prof.name}: `
        + (others.length ? `${others.join(', ')} ${list === 'deny' ? 'block' : 'allow'} it too.`
          : 'no other project uses that profile today, but any project moved onto it will.'),
      confirmLabel: 'Move', danger: list === 'allow' && others.length > 0 })
    if (!ok) return
    // profile_id null: the project's own profile, resolved by the host
    try { await promoteToProfile(r.slug, host, { list }); refresh() } catch (err) { notifyError(err) }
  }

  // where an entry lives, in words: "project list", "profile list", "for 1 h"
  const fromText = (e) => {
    if (e.from === 'auto' && e.rule === 'once') {
      return { text: 'allowed for 1 h', title: 'you allowed it for an hour: it expires on its own unless you keep it' }
    }
    const f = POLICY_FROM[e.from] || { text: e.from }
    return { text: e.from === 'auto' ? 'auto allow' : `${f.text} list`, title: e.from === 'auto' ? (e.reason || f.title) : f.title }
  }
  const editProfile = <Link to="/security/profiles">Edit profile</Link>
  const allowRow = (r, e) => {
    const f = fromText(e)
    return (
      <div key={`a:${e.from}:${e.id ?? e.host}`} className={`nw-entry${e.blocked ? ' overruled' : ''}`}>
        <span className="mono" title={e.host}>{e.host}</span>
        <span className="nw-from" title={f.title}>
          {f.text}{WHO[e.source] ? `, ${WHO[e.source]}` : ''}
          {e.from === 'auto' && `, until ${tsShort(e.expires_at)}`}
          {e.blocked && ', overruled by a block'}
        </span>
        <span className="sq-row-actions">
          {e.from === 'auto' && <>
            <Button variant="ghost" title="keep it: move it onto the project's list"
                    onClick={() => keep(e)}>Keep</Button>
            <Button variant="ghost" danger onClick={() => revoke(r, e)}>Revoke</Button></>}
          {e.from === 'project' && <>
            <Button variant="ghost" title={`move it to the ${r.profile.name} profile's always-allow list`}
                    onClick={() => promote(r, e.host, 'allow')}>Move to profile</Button>
            <Button variant="ghost" danger onClick={() => revoke(r, e)}>Remove</Button></>}
          {(e.from === 'profile' || e.from === 'general') && e.source !== 'seed' && editProfile}
        </span>
      </div>
    )
  }
  const blockRow = (r, e) => {
    const f = fromText(e)
    return (
      <div key={`d:${e.from}:${e.host}`} className="nw-entry">
        <span className="mono" title={e.host}>{e.host}</span>
        <span className="nw-from" title={f.title}><State tone="warn">Always blocked</State> {f.text}</span>
        <span className="sq-row-actions">
          {e.from === 'project' ? <>
            <Button variant="ghost" title={`move it to the ${r.profile.name} profile's always-block list`}
                    onClick={() => promote(r, e.host, 'deny')}>Move to profile</Button>
            <Button variant="ghost" onClick={() => revoke(r, e, 'deny')}>Remove</Button></>
            : editProfile}
        </span>
      </div>
    )
  }

  return (
    <Section title="Always allow and always block"
             actions={<Link className="ghost-link" to="/security/profiles">Edit profiles</Link>}>
      <details className="nw-how">
        <summary>How a site is judged</summary>
        <p>{ORDER}</p>
      </details>
      {groups && rows.length === 0 && <p className="sq-lede">No projects yet.</p>}
      {rows.map((r) => (
        <Group key={r.slug}>
          <div className="nw-proj-head">
            <b>{r.name}</b>
            <span className="sq-sub">
              {r.profile.isDefault ? 'Default profile' : `${r.profile.name} profile`}, new
              sites: {newSitesText(r.profile)}. {r.allow.length} always allowed, {r.block.length} always blocked.
            </span>
            <Button variant="link" onClick={() => addBlock(r)}>Block a site</Button>
          </div>
          {r.allow.length === 0 && r.block.length === 0 && (
            <div className="nw-none">Nothing listed: every site follows the new-sites rule.</div>)}
          {r.block.map((e) => blockRow(r, e))}
          {r.allow.filter((e) => e.source !== 'seed').map((e) => allowRow(r, e))}
          <BuiltIns entries={r.allow.filter((e) => e.source === 'seed')}
                    render={(e) => allowRow(r, e)} />
        </Group>
      ))}
    </Section>
  )
}

// ---- per-project egress policy editor ---------------------------------------
// The project's own always-allow / always-block lists, on top of its
// profile's. Decision order (backend/egress.py decide): cut, network off,
// project block, profile block, project allow, profile allow, live auto
// allow, the profile's new-sites rule.
function PolicyEditor({ slug }) {
  const [pol, setPol] = useState(null)
  const [allowText, setAllowText] = useState('')
  const [denyText, setDenyText] = useState('')
  const [status, setStatus] = useState('')
  const [saving, setSaving] = useState(false)

  function load() {
    getPolicy(slug).then((p) => {
      setPol(p); setAllowText(p.project_allow.join('\n')); setDenyText(p.project_deny.join('\n'))
    }).catch(() => setPol(null))
  }
  useEffect(() => { load() }, [slug]) // eslint-disable-line

  async function save() {
    setSaving(true)
    try {
      await putPolicy(slug, parseHosts(allowText), parseHosts(denyText))
      setStatus('Saved'); setTimeout(() => setStatus(''), 1500); load()
    } catch (err) { notifyError(err) }
    setSaving(false)
  }

  if (!pol) return null
  if (pol.fixed || slug === GENERAL) {
    return (
      <div className="nw-card">
        <h4>Project lists</h4>
        <p>{pol.fixed
          ? <>The image builders have a fixed policy that reaches package registries only. It cannot be edited.</>
          : <>This is the Default profile's list: edit it on <Link to="/security/profiles">Profiles</Link>.</>}</p>
        {pol.effective_allow.length > 0 && (
          <p>Always allows <span className="mono">{pol.effective_allow.join(', ')}</span></p>)}
      </div>
    )
  }
  return (
    <div className="nw-card">
      <h4>Project lists <span>{status}</span></h4>
      <p>
        On the <b>{pol.profile?.name || 'Default'}</b> profile, new sites: {newSitesText({
          network_off: pol.profile?.network_off, default_verdict: pol.profile?.default })}.{' '}
        <Link to="/security/profiles">Edit profile</Link>
      </p>
      <div className="nw-two">
        <Input textarea label="Always allow, this project (one site per line)" className="md-editor" rows={4}
               spellCheck={false} value={allowText} onChange={(e) => setAllowText(e.target.value)} />
        <Input textarea label="Always block, this project (a block wins)" className="md-editor" rows={4}
               spellCheck={false} value={denyText} onChange={(e) => setDenyText(e.target.value)} />
      </div>
      <div className="nw-foot">
        <span>
          {pol.effective_allow.length > 0 && <>With the profile, always allowed: <span className="mono">{pol.effective_allow.join(', ')}</span>. </>}
          {pol.effective_deny.length > 0 && <>Always blocked: <span className="mono">{pol.effective_deny.join(', ')}</span>.</>}
        </span>
        <Button disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save lists'}</Button>
      </div>
    </div>
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
      <PolicyLists project={slug} names={{}} projects={[]} tick={tick} onChanged={bump} />
      <div>
        <PolicyEditor slug={slug} />
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
      <PolicyLists project={filter} names={names} projects={projects} tick={tick} onChanged={bump} />

      {filter ? (
        <Section title={`${names[filter] || filter}: lists, LAN access, box and secrets`}>
          <div>
            <PolicyEditor slug={filter} />
            <LanAccess slug={filter} />
            <RunsIn slug={filter} />
            <Grants slug={filter} />
          </div>
        </Section>
      ) : (
        <p className="sq-lede">Choose a project at the top to edit its own lists, its LAN access,
          the box it runs in and its secret grants.</p>
      )}
    </div>
  )
}
