import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api, subscribeSse } from './api.js'
import SecurityBoard from './SecurityBoard.jsx'
import RunCards from './SecurityRuns.jsx'
import { HostRequest, useEgressDecide } from './EgressDecide.jsx'
import { notify, notifyError } from './notify.js'
import { useAsk } from './ask.jsx'
import { ago, ts } from './format.js'
import { diffLines } from './gitPage.js'
import { liveInQueue } from './securityQueue.js'
import { askAge } from './securityCopy.js'
import { cardsFor, cardTotals, chatHref, resolveAsk } from './securityRuns.js'
import {
  STEP_KINDS, approveLabel, approveOpensInspect, changeText, denyLabel, detailTitle, glanceBoxes,
  queueRows, rolesOf, rowState,
} from './securityDash.js'
import { Facts, State } from './securityParts.jsx'
import { Button, EmptyState, HelpTip, SidePanel, useRowMenu } from './components/index.js'
import { SERVICES_POLL_MS, followServices, listServices, rejectService } from './boxes/api/services.js'
import { listPackages } from './boxes/api/packages.js'
import { approveEnvironment, listEnvironments, rejectEnvironment } from './boxes/api/environments.js'
import {
  approveSkillRequest, fetchSkillRequest, listSkillRequests, rejectSkillRequest,
} from './boxes/api/skillRequests.js'
import { approveHeldJournal, dropHeldJournal, listHeldJournal } from './boxes/api/journalHeld.js'
import { approvePage, listPageRequests, listPages, rejectPage } from './boxes/api/pages.js'
import { listProfiles } from './boxes/api/profiles.js'
import { listImages } from './boxes/api/images.js'
import { followBoxes, listBoxes } from './boxes/api/vms.js'
import {
  EnvironmentCard, JournalHeldCard, PackageApprove, PackageSummary, PageLiveCard, PagePublishCard,
  ServiceRequest, SkillRequestCard, usePackageReject,
} from './boxes/RequestCards.jsx'

// Security as a dashboard: the things that need the operator, one quiet row
// each (what it is, for which project, the agent's two-sentence summary), and
// a click opens the detail beside the list: the summary, then Deny, Inspect,
// Approve. Inspect is the evidence: the chat step that asked, and for git the
// diff. The decisions go to the same endpoints as before; only the look moved.
//
// EVERY string from a request (summaries, reasons, commit messages, hosts,
// package names, questions, alert text) is UNTRUSTED: the agent's, the guest's,
// or the internet's. All of it is placed as plain text nodes; nothing here goes
// through <Md>.

const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) })
const changed = () => window.dispatchEvent(new Event('jarvis-files-changed'))

// The kinds whose Inspect is the full request card, with its own decision
// buttons (a form, or the alert's own actions): the panel's foot steps aside there
const CARD_INSPECT = new Set(['service', 'environment', 'skill', 'page', 'journal', 'site', 'alert'])

// ---- the queue: everything waiting, as rows, and the detail on click -----------

export function QueueList({ slug }) {
  const [slugs, setSlugs] = useState(slug ? [slug] : null)
  const [names, setNames] = useState({})
  const [git, setGit] = useState({})                 // slug -> pending requests
  const [sites, setSites] = useState([])
  const [runs, setRuns] = useState([])
  const [asks, setAsks] = useState([])
  const [services, setServices] = useState([])
  const [lan, setLan] = useState(null)
  const [packages, setPackages] = useState([])
  const [environments, setEnvironments] = useState([])
  const [skills, setSkills] = useState([])
  const [journal, setJournal] = useState([])
  const [pages, setPages] = useState([])
  const [profiles, setProfiles] = useState([])
  const [variants, setVariants] = useState(null)
  const [approvingPkg, setApprovingPkg] = useState(null)
  const [openKey, setOpenKey] = useState(null)
  const [tab, setTab] = useState('summary')
  const [board, setBoard] = useState(null)           // {id, seed}: the evidence board
  const [showQuiet, setShowQuiet] = useState(false)
  const [busy, setBusy] = useState(false)
  const reload = useRef(null)
  const loc = useLocation()
  const ask = useAsk()

  // a security toast deep-links an event's board via router state (openEvent)
  useEffect(() => {
    const id = loc.state?.openEvent
    if (id) setBoard({ id, seed: null })
  }, [loc.key]) // eslint-disable-line

  useEffect(() => {
    if (slug) { setSlugs([slug]); return }
    api('/api/projects').then((r) => {
      const ps = r.projects || []
      setSlugs(ps.map((p) => p.slug))
      setNames(Object.fromEntries(ps.map((p) => [p.slug, p.name])))
    }).catch(() => setSlugs([]))
  }, [slug])

  const mine = (rows, key = 'project_slug') => (slug ? rows.filter((r) => r[key] === slug) : rows)
  function loadGit(s) {
    api(`/api/projects/${s}/git/requests`).then((r) =>
      setGit((m) => ({ ...m, [s]: (r.requests || []).filter((q) => q.status === 'pending') })))
      .catch(() => {})
  }
  function loadSites() {
    api(`/api/egress/pending${slug ? `?project=${encodeURIComponent(slug)}` : ''}`)
      .then((r) => setSites(r.pending || [])).catch(() => {})
  }
  function loadAsks() {
    if (slug) return
    api('/api/notifications').then((r) => setAsks(r.asks || [])).catch(() => {})
  }
  function loadAlerts() {
    api('/api/security/runs?events=8').then((r) => setRuns(cardsFor(r.runs || [], slug))).catch(() => {})
  }
  function loadServices() {
    listServices(slug || undefined).then((r) => {
      setServices(r.services.filter((x) => x.status === 'pending'))
      setLan({ ip: r.lanIp, configured: r.lanConfigured, error: r.lanError })
    }).catch(() => setServices([]))
  }
  // a route an older server lacks just leaves its kind out
  function loadAgentReqs() {
    listPackages('pending').then((rows) => setPackages(mine(rows))).catch(() => setPackages([]))
    listEnvironments('pending', slug || undefined).then((rows) => setEnvironments(mine(rows)))
      .catch(() => setEnvironments([]))
    Promise.all([listSkillRequests('pending', slug || undefined), listSkillRequests('fetched', slug || undefined)])
      .then(([a, b]) => setSkills(mine([...a, ...b])))
      .catch(() => setSkills([]))
    listHeldJournal(slug || undefined).then(setJournal).catch(() => setJournal([]))
    listPageRequests().then((rows) => setPages(mine(rows, 'project'))).catch(() => setPages([]))
  }
  const refresh = useCallback(() => {
    (slugs || []).forEach(loadGit)
    loadSites(); loadAlerts(); loadAgentReqs(); loadAsks(); loadServices()
  }, [slugs]) // eslint-disable-line

  useEffect(() => {
    listProfiles().then(setProfiles).catch(() => {})
    listImages().then((r) => setVariants(r.variants)).catch(() => {})
  }, [])

  const key = slugs ? slugs.join(',') : ''
  useEffect(() => {
    if (!slugs) return undefined
    refresh()
    const t = setInterval(() => {
      slugs.forEach(loadGit); loadSites(); loadAlerts(); loadAgentReqs(); loadAsks()
    }, 12000)
    const tSvc = setInterval(loadServices, SERVICES_POLL_MS)
    const stopSvc = followServices(() => loadServices())
    window.addEventListener('jarvis-files-changed', refresh)
    return () => {
      clearInterval(t); clearInterval(tSvc); stopSvc()
      window.removeEventListener('jarvis-files-changed', refresh)
    }
  }, [key]) // eslint-disable-line

  // a live alert changes the cards: one refetch per burst
  useEffect(() => subscribeSse('/api/security/stream', (ev) => {
    if (ev.type !== 'security_event' || !liveInQueue(ev)) return
    const proj = ev.project_slug || ev.project
    if (slug && proj !== slug) return
    clearTimeout(reload.current)
    reload.current = setTimeout(() => loadAlerts(), 600)
  }), [slug]) // eslint-disable-line
  useEffect(() => () => clearTimeout(reload.current), [])

  const gitRows = (slugs || []).flatMap((s) => (git[s] || []).map((r) => ({ ...r, slug: s })))
  const { rows, quiet } = queueRows({
    git: gitRows, services, packages, environments, skills, journal, pages, sites, asks, runs,
  })
  const all = [...rows, ...quiet]
  const row = openKey ? all.find((r) => r.key === openKey) : null
  // a decided row drains away: its panel goes with it
  useEffect(() => { if (openKey && slugs && !row) setOpenKey(null) }, [openKey, row, slugs])

  const projLabel = (s) => names[s] || s
  const open = (r) => { setOpenKey(r.key); setTab('summary') }
  const close = () => setOpenKey(null)
  const done = () => { changed(); refresh() }

  const { decide: siteDecide, picker } = useEgressDecide(loadSites, { project: slug || null, names })
  const rejectPkg = usePackageReject(done)
  const placementOf = (proj) => {
    const p = profiles.find((x) => (x.projects || []).includes(proj)) || profiles.find((x) => x.is_default)
    return p?.service_placement || ''
  }

  // bulk verdicts: sites reached hundreds; alerts too. One server call each.
  const totals = cardTotals(runs)
  async function sitesBulk(action) {
    const n = sites.length
    const q = {
      approve: `Allow all ${n} sites, including the ones Auto review flagged? Each goes on the `
        + "always-allow list of its project's profile, so every project on that profile can "
        + 'reach it. Addresses that can never be allowed, like the CNTRL host, are skipped.',
      reject: `Deny all ${n} sites? They stay blocked and come back if a box asks again.`,
      dismiss: `Clear all ${n} sites from this list? No decision is recorded; a site a box asks for again comes back.`,
    }[action]
    const label = { approve: 'Allow all', reject: 'Deny all', dismiss: 'Clear list' }[action]
    if (!await ask.confirm(q, { confirmLabel: label, danger: action !== 'dismiss' })) return
    setBusy(true)
    try {
      const r = await post('/api/egress/pending/bulk', { action, project: slug || null })
      if (r?.skipped) notify(`${r.skipped} skipped: they cannot be allowed`, { life: 8 })
      loadSites(); changed()
    } catch (e) { notifyError(e) }
    setBusy(false)
  }
  async function alertsBulk(reports) {
    const n = reports ? totals.reports : totals.need
    if (!await ask.confirm(reports ? `Mark all ${n} agent reports resolved?` : `Acknowledge all ${n} alerts?`,
                           { confirmLabel: reports ? 'Resolve all' : 'Acknowledge all' })) return
    setBusy(true)
    try {
      await post(`/api/security/events/ack_all?${reports ? 'only' : 'exclude'}=harness_fault`)
      loadAlerts(); changed()
    } catch (e) { notifyError(e) }
    setBusy(false)
  }
  // ack_all is global: inside one project's panel it would clear other
  // projects' alerts, so the alert verdicts stay off there
  const bulk = useRowMenu([
    sites.length > 1 && { caption: `${sites.length} sites` },
    sites.length > 1 && { label: 'Allow all', disabled: busy, onSelect: () => sitesBulk('approve') },
    sites.length > 1 && { label: 'Deny all', disabled: busy, onSelect: () => sitesBulk('reject') },
    sites.length > 1 && { label: 'Clear the list', disabled: busy, onSelect: () => sitesBulk('dismiss') },
    sites.length > 1 && !slug && (totals.need || totals.reports) && '-',
    !slug && totals.need > 0 && { label: `Acknowledge all ${totals.need} alerts`, disabled: busy,
      onSelect: () => alertsBulk(false) },
    !slug && totals.reports > 0 && { label: `Resolve all ${totals.reports} agent reports`, disabled: busy,
      onSelect: () => alertsBulk(true) },
  ], { label: 'Answer many at once' })
  const hasBulk = sites.length > 1 || (!slug && (totals.need > 0 || totals.reports > 0))

  if (!slugs) return <p className="sq-lede">Loading…</p>

  const n = rows.length + quiet.length
  return (
    <section className="sd-queue" aria-label="Waiting for you">
      <header className="sd-head">
        <h3>Waiting for you</h3>
        {n > 0 && <span className="sq-count">{n}</span>}
        {hasBulk && <div className="sd-bulk">{bulk.menu}</div>}
      </header>
      {n === 0 && (
        <EmptyState>Nothing is waiting for you. Requests to commit, reach a site, install a package,
          run a service or publish a page land here, and so do alerts.</EmptyState>
      )}
      {rows.length > 0 && (
        <ul className="list-divided sd-list">
          {rows.map((r) => <QueueRow key={r.key} row={r} open={openKey === r.key}
                                     project={projLabel(r.project)} showProject={!slug}
                                     onOpen={() => open(r)} />)}
        </ul>
      )}
      {quiet.length > 0 && (
        <div className="sd-quiet">
          <button type="button" className="sq-fold" aria-expanded={showQuiet}
                  onClick={() => setShowQuiet(!showQuiet)}>
            {showQuiet ? 'Hide' : 'Show'} {quiet.length} info alert{quiet.length === 1 ? '' : 's'} and
            agent report{quiet.length === 1 ? '' : 's'}</button>
          {showQuiet && (
            <ul className="list-divided sd-list">
              {quiet.map((r) => <QueueRow key={r.key} row={r} open={openKey === r.key}
                                          project={projLabel(r.project)} showProject={!slug}
                                          onOpen={() => open(r)} />)}
            </ul>
          )}
        </div>
      )}

      {row && (
        <Detail row={row} tab={tab} setTab={setTab} onClose={close} onDone={done}
                project={projLabel(row.project)} siteDecide={siteDecide}
                onApprovePkg={() => setApprovingPkg(row.raw)} onRejectPkg={() => rejectPkg(row.raw)}
                lan={lan} placement={placementOf(row.raw?.project_slug)}
                onOpenBoard={(a) => setBoard({ id: a.id, seed: a })} scoped={!!slug}
                reloadAlerts={loadAlerts} />
      )}
      <PackageApprove p={approvingPkg} variants={variants} onClose={() => setApprovingPkg(null)}
                      onDone={() => { setApprovingPkg(null); done() }} />
      {picker}
      {board && (
        <SecurityBoard eventId={board.id} seed={board.seed} onClose={() => setBoard(null)}
                       onAck={async (id) => {
                         try { await post(`/api/security/events/${id}/ack`); loadAlerts() }
                         catch (e) { notifyError(e) }
                       }} />
      )}
    </section>
  )
}

// One row: what it is and for which project, then the summary. The whole row
// opens the detail.
function QueueRow({ row, open, project, showProject, onOpen }) {
  const st = rowState(row)
  const when = row.kind === 'ask' ? askAge(row.ageS) : ago(row.at)
  const repeat = row.subject && row.subject !== row.summary
  return (
    <li>
      <button type="button" className={`sd-row${open ? ' open' : ''}`} onClick={onOpen}
              aria-haspopup="dialog" aria-expanded={open}>
        <span className="sd-row-top">
          <span className="sd-row-what">
            <b>{row.label}</b>
            {repeat && <> <span className={row.mono ? 'mono sd-subject' : 'sd-subject'}>{row.subject}</span></>}
            {showProject && row.project && <span className="sd-proj"> for {project}</span>}
          </span>
          {st && <span className={`sd-word ${st.tone}`}>{st.word}</span>}
          {when && <span className="sd-when" title={row.at ? `${ts(row.at)} UTC` : undefined}>{when}</span>}
        </span>
        {row.summary && <span className="sd-row-sum">{row.summary}</span>}
      </button>
    </li>
  )
}

// ---- the detail: summary, then Deny / Inspect / Approve ---------------------------

function Detail({
  row, tab, setTab, onClose, onDone, project, siteDecide, onApprovePkg, onRejectPkg, lan, placement,
  onOpenBoard, scoped, reloadAlerts,
}) {
  const ask = useAsk()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const r = row.raw || {}
  async function guard(fn) {
    setBusy(true)
    try { await fn() } catch (e) { notifyError(e) }
    setBusy(false)
  }
  const reason = (what) => ask.prompt(`Deny ${what}? Reason (optional)`, '', { confirmLabel: 'Deny' })

  const deny = () => guard(async () => {
    switch (row.kind) {
      case 'git':
        if (!await ask.confirm(`Deny ${row.label.toLowerCase()} #${row.id}?`, { confirmLabel: 'Deny', danger: true })) return
        await post(`/api/projects/${row.project}/git/requests/${row.id}/reject`)
        break
      case 'package': onRejectPkg(); return
      case 'service': { const why = await reason(`service ${r.name}`); if (why === null) return; await rejectService(r.id, why); break }
      case 'environment': { const why = await reason(`environment ${r.name}`); if (why === null) return; await rejectEnvironment(r.id, why); break }
      case 'skill': { const why = await reason(`skill request ${r.source}`); if (why === null) return; await rejectSkillRequest(r.id, why); break }
      case 'page': await rejectPage(r); notify('publish request denied'); break
      case 'journal': await dropHeldJournal(r); notify('journal entry dropped'); break
      case 'site': await siteDecide(r, 'deny'); return
      default: return
    }
    onDone()
  })

  const approve = () => guard(async () => {
    if (approveOpensInspect(row)) { setTab('inspect'); return }
    switch (row.kind) {
      case 'git': await post(`/api/projects/${row.project}/git/requests/${row.id}/approve`); break
      case 'package': onApprovePkg(); return
      case 'environment': {
        const use = row.sub === 'use'
        if (!await ask.confirm(use
          ? `Approve moving ${r.project_slug} onto image ${r.name}? Nothing is built or installed.`
          : `Approve environment ${r.name}? The host builds the image from ${r.from_variant} with the `
            + `pinned packages, then ${r.project_slug} boots on it.`,
        { confirmLabel: use ? 'Approve' : 'Approve and build' })) return
        const res = await approveEnvironment(r.id, { build: !use })
        notify(use ? `${r.project_slug} moves onto ${r.name} at its next box boot`
          : res?.build_started ? `building ${r.name}` : `approved; the build of ${r.name} did not start (busy?)`)
        break
      }
      case 'skill':
        if (r.status !== 'fetched') {
          await fetchSkillRequest(r.id)
          setTab('inspect')
          onDone()
          return
        }
        if (!await ask.confirm(`Install skill ${r.skill_name || r.source}? You have read SKILL.md `
          + `(sha256 ${String(r.skill_sha256).slice(0, 12)}). It is installed exactly as read and granted `
          + 'to agents if its requirements are met.', { confirmLabel: 'Install and grant' })) return
        {
          const res = await approveSkillRequest(r.id, r.skill_sha256)
          notify(res?.granted ? 'skill installed and granted' : `skill installed, not granted${res?.error ? `: ${res.error}` : ''}`)
        }
        break
      case 'page': {
        const res = await approvePage(r)
        notify(res?.url ? `published ${r.name}: ${res.url}` : `published ${r.name}`, { life: 10 })
        break
      }
      case 'journal': await approveHeldJournal(r); notify('journal entry approved'); break
      case 'site': await siteDecide(r, 'allow'); return
      case 'ask': navigate(`/c/${row.chat}`); return
      case 'alert': {
        if (r.counts?.need > 0) {
          const q = resolveAsk(r)
          if (!await ask.confirm(q.title, { body: q.body, confirmLabel: q.confirm })) return
        }
        await post(`/api/security/runs/${encodeURIComponent(r.key)}/ack`)
        changed(); reloadAlerts()
        return
      }
      default: return
    }
    onDone()
  })

  const denyWord = denyLabel(row)
  const approveWord = approveLabel(row)
  const inInspect = tab === 'inspect'
  const blocked = row.kind === 'site' && r.refused
  const footer = (inInspect && CARD_INSPECT.has(row.kind)) ? null : (
    <>
      {denyWord && <Button variant="ghost" disabled={busy} onClick={deny}>{denyWord}</Button>}
      {!inInspect && <Button variant="ghost" onClick={() => setTab('inspect')}>Inspect</Button>}
      {row.kind === 'site' && !blocked && (
        <Button variant="ghost" disabled={busy} onClick={() => guard(() => siteDecide(r, 'once'))}>Allow 1 h</Button>)}
      {approveWord && !blocked && (
        <Button disabled={busy} onClick={approve}>{approveWord}</Button>)}
    </>
  )
  return (
    <SidePanel open onClose={onClose} title={detailTitle(row)} className="sd-panel"
               width={inInspect ? 640 : 440}
               tabs={[{ id: 'summary', label: 'Summary' }, { id: 'inspect', label: 'Inspect' }]}
               tab={tab} onTab={setTab} footer={footer}>
      {(t) => (t === 'inspect'
        ? <Inspect row={row} onDone={onDone} siteDecide={siteDecide} lan={lan} placement={placement}
                   onOpenBoard={onOpenBoard} scoped={scoped} project={project} reloadAlerts={reloadAlerts} />
        : <Summary row={row} project={project} />)}
    </SidePanel>
  )
}

function Where({ row, project }) {
  const when = row.kind === 'ask' ? askAge(row.ageS) : ago(row.at)
  return (
    <p className="sq-meta">
      <b>{row.label}</b>
      {row.project && <> for {project}</>}
      {row.chat && <>, from <Link to={`/c/${row.chat}`}>chat {row.chat}</Link></>}
      {when && <>, {when}</>}
    </p>
  )
}

function Summary({ row, project }) {
  const st = rowState(row)
  return (
    <div className="sd-detail">
      <Where row={row} project={project} />
      {row.subject && row.subject !== row.summary && (
        <div className={`sq-title${row.mono ? ' mono' : ''}`}>{row.subject}</div>)}
      {row.summary && (
        <figure className="sd-summary">
          <figcaption>
            {row.byAgent ? "The agent's summary" : ({
              ask: 'The question', journal: 'The entry', alert: 'The newest alert',
            })[row.kind] || 'Reason'}
            {row.byAgent && (
              <HelpTip label="About the summary">The agent that asked wrote this, in at most two
                sentences. It is its own account: check it under Inspect.</HelpTip>)}
          </figcaption>
          <blockquote>{row.summary}</blockquote>
        </figure>
      )}
      {st && (
        <div className="sq-states">
          <State tone={st.tone} why={(st.why && st.why !== row.summary) ? st.why : undefined}>{st.word}</State>
        </div>)}
      {changeText(row) && <p className="sq-change">{changeText(row)}</p>}
    </div>
  )
}

// ---- Inspect: the step that asked, then the evidence ----------------------------

function Inspect({ row, onDone, siteDecide, lan, placement, onOpenBoard, scoped, project, reloadAlerts }) {
  const r = row.raw || {}
  return (
    <div className="sd-detail sd-inspect">
      <ChatStep row={row} />
      {row.kind === 'git' && <GitEvidence row={row} />}
      {row.kind === 'package' && <PackageSummary p={r} />}
      {row.kind === 'service' && <ServiceRequest s={r} lan={lan} profilePlacement={placement} onDone={onDone} />}
      {row.kind === 'environment' && <EnvironmentCard e={r} onDone={onDone} />}
      {row.kind === 'skill' && <SkillRequestCard r={r} onDone={onDone} />}
      {row.kind === 'page' && <PagePublishCard p={r} onDone={onDone} />}
      {row.kind === 'journal' && <JournalHeldCard h={r} onDone={onDone} />}
      {row.kind === 'site' && (
        <HostRequest p={r} onDecide={async (p, verb) => { await siteDecide(p, verb) }} scoped={scoped}
                     projectName={project} />)}
      {row.kind === 'ask' && <p className="sq-change">{r.question}</p>}
      {row.kind === 'alert' && (
        <RunCards runs={[r]} slug={scoped ? r.project : undefined} onChanged={reloadAlerts}
                  onOpenBoard={onOpenBoard} />)}
    </div>
  )
}

// "Filed at step #41 of chat 12": a link straight to it. Requests keep the call
// that filed them (GET /api/security/request_step); a question or a request
// filed outside a chat links to its chat, or says there is none.
function ChatStep({ row }) {
  const [where, setWhere] = useState(null)
  const known = STEP_KINDS.includes(row.kind)
  useEffect(() => {
    let live = true
    setWhere(null)
    if (!known) return undefined
    api(`/api/security/request_step?kind=${row.kind}&id=${encodeURIComponent(row.id)}`)
      .then((w) => { if (live) setWhere(w) })
      .catch(() => { if (live) setWhere({ conversation_id: row.chat, step: null }) })
    return () => { live = false }
  }, [row.key]) // eslint-disable-line
  if (row.kind === 'alert' || row.kind === 'site' || row.kind === 'journal') return null
  const cid = known ? (where?.conversation_id ?? null) : row.chat
  if (known && where === null) return <p className="sd-step dim">Finding the step that asked…</p>
  if (!cid) return <p className="sd-step dim">Not filed from a chat.</p>
  const step = where?.step
  return (
    <p className="sd-step">
      {step ? <>Asked at step #{step} of chat {cid}.</> : <>Asked in chat {cid}.</>}{' '}
      <Link to={chatHref(cid, step)}>{step ? 'Open the chat at this step' : 'Open the chat'}</Link>
    </p>
  )
}

// git: the diff it would commit, the pull request's diff, or the merge's
function GitEvidence({ row }) {
  const r = row.raw || {}
  const [s, setS] = useState({ loading: true })
  useEffect(() => {
    let live = true
    const slug = encodeURIComponent(row.project)
    let paths = r.paths
    if (typeof paths === 'string') { try { paths = JSON.parse(paths) } catch { paths = null } }
    let get
    if (row.sub === 'push' && r.pr_number) {
      get = api(`/api/gitea/repos/${slug}/pulls/${r.pr_number}/diff`)
        .then((d) => ({ diff: d.diff, truncated: d.truncated, url: d.pr_url }))
    } else if (row.sub === 'merge') {
      get = api(`/api/projects/${slug}/git/requests/${row.id}/merge`)
        .then((d) => ({ diff: d.diff, stat: d.diffstat, files: d.files }))
    } else if (row.sub === 'commit') {
      // new files whole, as added lines: most of what an agent commits is new
      const one = (p) => api(`/api/projects/${slug}/git/diff?new_files=true${p ? `&path=${encodeURIComponent(p)}` : ''}`)
        .then((d) => d.diff || '')
      get = (Array.isArray(paths) && paths.length ? Promise.all(paths.map(one)) : one(null).then((d) => [d]))
        .then((parts) => ({ diff: parts.join('\n'), paths }))
    } else {
      get = Promise.resolve({ diff: null })
    }
    get.then((d) => { if (live) setS({ ...d, loading: false }) })
      .catch((e) => { if (live) setS({ loading: false, error: e.detail || String(e) }) })
    return () => { live = false }
  }, [row.key]) // eslint-disable-line
  return (
    <>
      <Facts rows={[
        row.sub === 'remote' && ['Repository', r.message, true],
        row.sub !== 'remote' && ['Message', r.message],
        r.branch && ['Branch', r.branch, true],
        Array.isArray(s.paths) && s.paths.length > 0 && ['Paths', s.paths.join('  '), true],
        s.stat && ['Changes', s.stat, true],
        r.pr_url && ['Pull request', <a key="pr" href={r.pr_url} target="_blank" rel="noreferrer">{r.pr_url}</a>],
      ]} />
      {row.sub === 'commit' && (
        <p className="sq-change">The changes as they are now; files written after this request are in
          it too, since approving commits the live files.</p>)}
      {s.loading && <p className="sq-change">Loading the diff…</p>}
      {s.error && <p className="sq-change">Could not read the diff: {s.error}</p>}
      {s.diff != null && !s.loading && (
        <pre className="mem-diff git-diff-view sd-diff">
          {diffLines(s.diff || 'no changes').map((l, i) => (
            <span key={i} className={`mem-dl ${l.cls}`}>{l.text}</span>))}
        </pre>
      )}
      {s.truncated && s.url && (
        <p className="sq-change">Cut short. <a href={s.url} target="_blank" rel="noreferrer">Open it in Gitea</a> for the rest.</p>)}
    </>
  )
}

// ---- at a glance: the boxes that are running, and what works in them ------------

export function BoxesGlance() {
  const [data, setData] = useState(null)
  const [roles, setRoles] = useState({})
  const [names, setNames] = useState({})
  useEffect(() => {
    let live = true
    api('/api/projects').then((r) => {
      if (live) setNames(Object.fromEntries((r.projects || []).map((p) => [p.slug, p.name])))
    }).catch(() => {})
    const load = () => {
      listBoxes().then((r) => { if (live) setData(r) }).catch(() => { if (live) setData({ boxes: [] }) })
      api('/api/chat/agents?scope=active').then((r) => { if (live) setRoles(rolesOf(r.nodes || r.agents || [])) })
        .catch(() => {})
    }
    load()
    const t = setInterval(load, 15000)
    const stop = followBoxes(() => load())
    return () => { live = false; clearInterval(t); stop() }
  }, [])
  if (!data) return null
  const { running, stopped } = glanceBoxes(data.boxes, roles)
  return (
    <section className="sq-rail-sec" aria-label="Boxes">
      <h3 className="sq-rail-h">
        Boxes running
        <Link className="sd-edit" to="/security/profiles" title="what each project's boxes may do">Edit profiles</Link>
      </h3>
      {running.length === 0 ? (
        <p>No box is running.{stopped ? ` ${stopped} stopped.` : ''} One starts when a chat or agent needs it.</p>
      ) : (
        <ul className="list-divided sd-boxes">
          {running.map((b) => (
            <li key={b.id}>
              <div className="sd-box-top">
                <span className="sd-box-name">
                  {b.kind === 'project' ? `${names[b.project] || b.project} box` : b.name}</span>
                <span className="mono sd-box-img" title={`image ${b.image}, ${b.runtime === 'docker' ? 'container' : 'virtual machine'}`}>
                  {b.image}</span>
              </div>
              {b.work.length === 0 ? <div className="sd-box-idle">{b.state === 'busy' ? 'Busy' : 'Idle'}</div> : (
                <ul className="sd-box-work">
                  {b.work.map((w) => (
                    <li key={w.key}>
                      {w.project && <b>{names[w.project] || w.project}</b>}
                      {w.project && (w.title || w.conversationId) && ': '}
                      {w.conversationId
                        ? <Link to={`/c/${w.conversationId}`}>{w.title || `chat ${w.conversationId}`}</Link>
                        : (w.title || 'a turn')}
                      {w.role && <span className="sd-role"> {w.role}</span>}
                      {w.tool && <span className="mono sd-tool"> {w.tool}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
      {running.length > 0 && stopped > 0 && <p>{stopped} more stopped. <Link to="/vms">All boxes</Link></p>}
    </section>
  )
}

// Published pages: live now, each a click from Unpublish
export function LivePages() {
  const [pages, setPages] = useState([])
  const [openId, setOpenId] = useState(null)
  const load = useCallback(() => { listPages().then(setPages).catch(() => setPages([])) }, [])
  useEffect(() => {
    load()
    window.addEventListener('jarvis-files-changed', load)
    return () => window.removeEventListener('jarvis-files-changed', load)
  }, [load])
  if (!pages.length) return null
  const p = pages.find((x) => x.id === openId)
  return (
    <section className="sq-rail-sec" aria-label="Published pages">
      <h3 className="sq-rail-h">Published pages <span className="sq-count">{pages.length}</span></h3>
      <ul className="list-divided sd-pages">
        {pages.map((x) => (
          <li key={x.id}>
            <button type="button" className="sd-page" onClick={() => setOpenId(x.id)}>
              <span>{x.name}</span><span className="sd-proj">{x.project}</span>
            </button>
          </li>
        ))}
      </ul>
      {p && (
        <SidePanel open onClose={() => setOpenId(null)} title="Published page" width={440}>
          <PageLiveCard p={p} onDone={() => { setOpenId(null); load() }} />
        </SidePanel>
      )}
    </section>
  )
}
