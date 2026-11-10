import { useContext, useEffect, useRef, useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'
import { api, subscribeSse } from '../api.js'
import SecurityBoard from '../SecurityBoard.jsx'
import SecurityHistory from '../SecurityHistory.jsx'
import RunCards from '../SecurityRuns.jsx'
import TriagePanel from '../TriagePanel.jsx'
import Posture from '../Posture.jsx'
import ScrollHint from '../ScrollHint.jsx'
import { HostRequest, useEgressDecide } from '../EgressDecide.jsx'
import { PendingCountContext } from '../Notices.jsx'
import { notify, notifyError } from '../notify.js'
import { useAsk } from '../ask.jsx'
import { ago } from '../format.js'
import { liveInQueue } from '../securityQueue.js'
import { cardsFor, cardTotals } from '../securityRuns.js'
import { ASKS_LEDE, askAge, ledeFor, SECURITY_LEDES } from '../securityCopy.js'
import { Group, Item, Section, State } from '../securityParts.jsx'
import Page from '../components/Page.jsx'
import Tabs from '../components/Tabs.jsx'
import Button from '../components/Button.jsx'
import { SERVICES_POLL_MS, followServices, listServices } from '../boxes/api/services.js'
import { listPackages } from '../boxes/api/packages.js'
import { listEnvironments } from '../boxes/api/environments.js'
import { listSkillRequests } from '../boxes/api/skillRequests.js'
import { listHeldJournal } from '../boxes/api/journalHeld.js'
import { listPageRequests, listPages } from '../boxes/api/pages.js'
import { listProfiles } from '../boxes/api/profiles.js'
import { listImages } from '../boxes/api/images.js'
import {
  EnvironmentCard, JournalHeldCard, PackageApprove, PackageRequest, PageLiveCard, PagePublishCard,
  ServiceRequest, SkillRequestCard, usePackageReject,
} from '../boxes/RequestCards.jsx'

// One cross-project queue of everything awaiting the operator: git commit
// requests, egress host approvals, and security alerts (which now include the
// advisory write flags — file writes apply live, the diff gate alerts instead
// of blocking). Rendered whole on the global /security page and, with a `slug`,
// filtered to a single project inside a Workspace panel.
//
// EVERY string in here — flag triggers/details, commit messages, egress hosts,
// alert summaries/details — is UNTRUSTED (it comes from the agent, from the
// guest, or from scanned/egress data). All of it is rendered as plain text
// nodes; nothing here goes through <Md>.
//
// An alert row is a headline, not the evidence: "Inspect" opens the
// SecurityBoard, which is where the flagged code, the diff, the directory and
// the traffic live. A security toast deep-links straight into it via router
// state (`openEvent`), so a card that drains away is still recoverable.

export function ReviewQueue({ slug }) {
  const [slugs, setSlugs] = useState(slug ? [slug] : null)  // project slugs to cover
  const [names, setNames] = useState({})                     // slug -> display name
  const [gitReqs, setGitReqs] = useState({})                 // slug -> [pending requests]
  const [pending, setPending] = useState([])                 // egress host approvals
  const [runs, setRuns] = useState([])                       // the Queue's cards, one per run
  const reload = useRef(null)                                // a live event schedules one refetch
  const [asks, setAsks] = useState([])                       // questions waiting in chats
  const [busy, setBusy] = useState(false)
  const [board, setBoard] = useState(null)   // {id, seed} — the open evidence board
  // the boxes requests (WP3 services, WP5 packages). Either route may not
  // exist on this server yet: then the section simply never shows.
  const [svcReqs, setSvcReqs] = useState([])
  const [lan, setLan] = useState(null)       // {ip, configured, error}
  const [pkgReqs, setPkgReqs] = useState([])
  const [envReqs, setEnvReqs] = useState([])
  const [skillReqs, setSkillReqs] = useState([])
  const [heldJournal, setHeldJournal] = useState([])
  const [pageReqs, setPageReqs] = useState([])
  const [livePages, setLivePages] = useState([])
  const [profiles, setProfiles] = useState([])
  const [variants, setVariants] = useState(null)
  const [approvingPkg, setApprovingPkg] = useState(null)
  const loc = useLocation()
  const ask = useAsk()

  // arriving from a security toast: open that event's board straight away.
  // Keyed on loc.key as well as the id so clicking a second toast for the SAME
  // alert re-opens it instead of looking dead.
  useEffect(() => {
    const id = loc.state?.openEvent
    if (id) setBoard({ id, seed: null })
  }, [loc.key]) // eslint-disable-line

  // which projects to cover: the one slug, or all of them
  useEffect(() => {
    if (slug) { setSlugs([slug]); return }
    api('/api/projects').then((r) => {
      const ps = r.projects || []
      setSlugs(ps.map((p) => p.slug))
      const nm = {}; ps.forEach((p) => { nm[p.slug] = p.name })
      setNames(nm)
    }).catch(() => setSlugs([]))
  }, [slug])

  function loadProject(s) {
    api(`/api/projects/${s}/git/requests`).then((r) =>
      setGitReqs((m) => ({ ...m, [s]: (r.requests || []).filter((q) => q.status === 'pending') })))
      .catch(() => {})
  }
  function loadEgress() {
    api(`/api/egress/pending${slug ? `?project=${encodeURIComponent(slug)}` : ''}`)
      .then((r) => setPending(r.pending || [])).catch(() => {})
  }
  // questions an agent is blocked on in a chat: the nav badge counts them, so
  // the page lists them (with a link to answer), or the badge runs one ahead
  function loadAsks() {
    if (slug) return
    api('/api/notifications').then((r) => setAsks(r.asks || [])).catch(() => {})
  }
  function loadAlerts() {
    // the Queue: one card per run (backend/secruns.py), each with its newest
    // events and what the agent was doing at each. Audit lines stay in the History.
    api('/api/security/runs?events=8').then((r) => {
      setRuns(cardsFor(r.runs || [], slug))
    }).catch(() => {})
  }

  function loadSvcReqs() {
    listServices(slug || undefined).then((r) => {
      setSvcReqs(r.services.filter((x) => x.status === 'pending'))
      setLan({ ip: r.lanIp, configured: r.lanConfigured, error: r.lanError })
    }).catch(() => setSvcReqs([]))
  }
  function loadBoxReqs() {
    loadSvcReqs()
    loadPkgReqs()
    loadAgentReqs()
  }
  // environment and skill-install requests (older servers: the sections never show)
  function loadAgentReqs() {
    listEnvironments('pending', slug || undefined).then((rows) =>
      setEnvReqs(slug ? rows.filter((r) => r.project_slug === slug) : rows))
      .catch(() => setEnvReqs([]))
    Promise.all([listSkillRequests('pending', slug || undefined), listSkillRequests('fetched', slug || undefined)])
      .then(([a, b]) => {
        const rows = [...a, ...b].sort((x, y) => x.id - y.id)
        setSkillReqs(slug ? rows.filter((r) => r.project_slug === slug) : rows)
      }).catch(() => setSkillReqs([]))
    listHeldJournal(slug || undefined).then(setHeldJournal).catch(() => setHeldJournal([]))
    // published pages (older servers: the sections never show)
    listPageRequests().then((rows) => setPageReqs(slug ? rows.filter((r) => r.project === slug) : rows))
      .catch(() => setPageReqs([]))
    listPages().then((rows) => setLivePages(slug ? rows.filter((r) => r.project === slug) : rows))
      .catch(() => setLivePages([]))
  }
  function loadPkgReqs() {
    listPackages('pending').then((rows) =>
      setPkgReqs(slug ? rows.filter((r) => r.project_slug === slug) : rows))
      .catch(() => setPkgReqs([]))
  }
  useEffect(() => {
    listProfiles().then(setProfiles).catch(() => {})
    listImages().then((r) => setVariants(r.variants)).catch(() => {})
  }, [])
  const rejectPkg = usePackageReject(loadBoxReqs)
  const placementOf = (proj) => {
    const p = profiles.find((x) => (x.projects || []).includes(proj))
      || profiles.find((x) => x.is_default)
    return p?.service_placement || ''
  }

  const key = slugs ? slugs.join(',') : ''
  useEffect(() => {
    if (!slugs) return
    // service requests ride topic `services` (below) with a slow fallback
    // poll; everything else keeps the 12 s refresh
    const refresh = () => {
      slugs.forEach(loadProject); loadEgress(); loadAlerts(); loadPkgReqs(); loadAgentReqs(); loadAsks()
    }
    refresh()
    loadSvcReqs()
    const t = setInterval(refresh, 12000)
    const tSvc = setInterval(loadSvcReqs, SERVICES_POLL_MS)
    const stopSvc = followServices(() => loadSvcReqs())
    const h = () => { refresh(); loadSvcReqs() }
    window.addEventListener('jarvis-files-changed', h)
    return () => {
      clearInterval(t); clearInterval(tSvc); stopSvc()
      window.removeEventListener('jarvis-files-changed', h)
    }
  }, [key]) // eslint-disable-line

  // a live security alert changes a card's counts and its newest events: refetch
  // the cards once (a burst of events is one request, not one each)
  useEffect(() => {
    return subscribeSse('/api/security/stream', (ev) => {
      if (ev.type !== 'security_event') return
      // filed already acknowledged (their own action, a rule's "normal work", or
      // a Record-only kind) or an audit line: it is history, not a queue item
      if (!liveInQueue(ev)) return
      const proj = ev.project_slug || ev.project
      if (slug && proj !== slug) return
      clearTimeout(reload.current)
      reload.current = setTimeout(() => loadAlerts(), 600)
    })
  }, [slug]) // eslint-disable-line
  useEffect(() => () => clearTimeout(reload.current), [])

  async function gitAct(s, id, verb) {
    if (verb === 'reject'
        && !await ask.confirm(`Reject commit request #${id}?`,
                              { confirmLabel: 'Reject', danger: true })) return
    setBusy(true)
    try {
      await api(`/api/projects/${s}/git/requests/${id}/${verb}`, { method: 'POST' })
      loadProject(s)
      window.dispatchEvent(new Event('jarvis-files-changed'))
    } catch (e) { notifyError(e) }
    setBusy(false)
  }
  // Allow always / Allow 1 h / Deny, the same three words and the same project
  // picker as the Network tab (an unattributed row is put on a project you choose)
  const { decide: egressAct, picker } = useEgressDecide(loadEgress, { project: slug || null, names })
  async function ackAlert(id) {
    try { await api(`/api/security/events/${id}/ack`, { method: 'POST' }); loadAlerts() }
    catch (e) { notifyError(e) }
  }

  // bulk verdicts — the queues reached hundreds; one server call each.
  // Allow all trains the allowlist for every host, so it confirms hardest.
  const BULK_ASK = {
    approve: (n) => `Allow all ${n} sites? Each goes on its project's always-allow list, `
      + 'including the ones Auto review flagged. Addresses that can never be allowed, like the CNTRL '
      + 'host, are skipped.',
    reject: (n) => `Deny all ${n} sites? They stay blocked and come back if a box asks again.`,
    dismiss: (n) => `Clear all ${n} sites from this list? No decision is recorded; `
      + 'a site a box asks for again comes back.',
  }
  const BULK_LABEL = { approve: 'Allow all', reject: 'Deny all', dismiss: 'Clear list' }
  async function egressBulk(action) {
    if (!await ask.confirm(BULK_ASK[action](pending.length),
                           { confirmLabel: BULK_LABEL[action],
                             danger: action !== 'dismiss' })) return
    setBusy(true)
    try {
      const r = await api('/api/egress/pending/bulk', {
        method: 'POST',
        body: JSON.stringify({ action, project: slug || null }) })
      if (r?.skipped) notify(`${r.skipped} skipped: they cannot be allowed`, { life: 8 })
      loadEgress()
      window.dispatchEvent(new Event('jarvis-files-changed'))
    } catch (e) { notifyError(e) }
    setBusy(false)
  }
  // Agent reports (harness_fault) are a list of their own: the alerts' bulk
  // acknowledge leaves them alone, and they have their own "Resolve all".
  async function ackAllAlerts(only) {
    const n = only ? totals.reports : totals.need
    if (!await ask.confirm(only ? `Mark all ${n} agent reports resolved?`
                                : `Acknowledge all ${n} alerts?`,
                           { confirmLabel: only ? 'Resolve all' : 'Acknowledge all' })) return
    setBusy(true)
    try {
      await api(`/api/security/events/ack_all?${only ? 'only' : 'exclude'}=harness_fault`,
                { method: 'POST' })
      loadAlerts()
      window.dispatchEvent(new Event('jarvis-files-changed'))
    } catch (e) { notifyError(e) }
    setBusy(false)
  }

  const totals = cardTotals(runs)
  const multi = !slug && (slugs?.length || 0) > 1
  const projLabel = (s) => names[s] || s
  const gitTotal = (slugs || []).reduce((n, s) => n + (gitReqs[s]?.length || 0), 0)
  const total = totals.need + totals.reports + gitTotal + pending.length + svcReqs.length + pkgReqs.length
    + envReqs.length + skillReqs.length + heldJournal.length + pageReqs.length + asks.length

  if (!slugs) return <p className="sq-lede">Loading…</p>

  const gitRows = (slugs || []).flatMap((s) => (gitReqs[s] || []).map((r) => ({ ...r, slug: s })))
  return (
    <div className="sq-queue">
      {total === 0 && (
        <div className="sq-empty">
          <b>Nothing is waiting for you.</b>
          When an agent asks to commit, reach a new site, install a package, run a service or
          publish a page, the request lands here. So do security alerts.
        </div>
      )}

      {/* ---- git: commits and merge requests (deliberately no bulk verdict:
             each one changes a repo, they deserve individual eyes) ---- */}
      {gitTotal > 0 && (
        <Section title="Commits and merges" count={gitTotal}>
          <Group>
            {gitRows.map((r) => <GitRequest key={`${r.slug}:${r.id}`} r={r} busy={busy}
                                            project={projLabel(r.slug)} showProject={multi || !slug}
                                            onAct={(verb) => gitAct(r.slug, r.id, verb)} />)}
          </Group>
        </Section>
      )}

      {/* ---- services: each one individually, never in bulk, and never
             auto-handled (the reviewer's never-list) ---- */}
      {svcReqs.length > 0 && (
        <Section title="Services" count={svcReqs.length}>
          <Group>
            {svcReqs.map((x) => (
              <ServiceRequest key={x.id} s={x} lan={lan}
                              profilePlacement={placementOf(x.project_slug)}
                              onDone={loadBoxReqs} />
            ))}
          </Group>
        </Section>
      )}

      {pkgReqs.length > 0 && (
        <Section title="Packages" count={pkgReqs.length}>
          <Group>
            {pkgReqs.map((p) => (
              <PackageRequest key={p.id} p={p} onApprove={() => setApprovingPkg(p)}
                              onReject={() => rejectPkg(p)} />
            ))}
          </Group>
          <PackageApprove p={approvingPkg} variants={variants}
                          onClose={() => setApprovingPkg(null)}
                          onDone={() => { setApprovingPkg(null); loadBoxReqs() }} />
        </Section>
      )}

      {envReqs.length > 0 && (
        <Section title="Environments" count={envReqs.length}>
          <Group>{envReqs.map((e) => <EnvironmentCard key={e.id} e={e} onDone={loadBoxReqs} />)}</Group>
        </Section>
      )}

      {skillReqs.length > 0 && (
        <Section title="Skills" count={skillReqs.length}>
          <Group>{skillReqs.map((r) => <SkillRequestCard key={r.id} r={r} onDone={loadBoxReqs} />)}</Group>
        </Section>
      )}

      {heldJournal.length > 0 && (
        <Section title="Journal entries held back" count={heldJournal.length}>
          <Group>
            {heldJournal.map((h) => <JournalHeldCard key={`${h.project}:${h.id}`} h={h} onDone={loadBoxReqs} />)}
          </Group>
        </Section>
      )}

      {pageReqs.length > 0 && (
        <Section title="Pages to publish" count={pageReqs.length}>
          <Group>{pageReqs.map((p) => <PagePublishCard key={p.id} p={p} onDone={loadBoxReqs} />)}</Group>
        </Section>
      )}

      {/* ---- questions in chats: answered there, listed here so the count matches ---- */}
      {asks.length > 0 && (
        <Section title="Questions in chats" count={asks.length} lede={ASKS_LEDE}>
          <Group>
            {asks.map((k) => (
              <Item key={k.id}
                    meta={<><b>{k.kind === 'permission' ? 'Permission' : 'Question'}</b> in
                      chat {k.conversation_id}, {askAge(k.age_s)}</>}
                    title={k.question} titleAttr={k.question}
                    actions={<Link className="ghost-link" to={`/c/${k.conversation_id}`}>
                      Answer in chat</Link>} />
            ))}
          </Group>
        </Section>
      )}

      {/* ---- egress host approvals ---- */}
      {pending.length > 0 && (
        <Section title="Sites a box asked for" count={pending.length}
                 lede={'A box tried to reach these and no list covers them, so they were blocked. '
                   + '"Allow always" puts the site on that project\'s always-allow list; '
                   + '"Allow 1 h" lets it through for an hour and writes no list.'}
                 actions={pending.length > 1 && <>
                   <Button variant="ghost" disabled={busy}
                           title="empty the list without deciding anything"
                           onClick={() => egressBulk('dismiss')}>Clear list</Button>
                   <Button variant="ghost" disabled={busy} title="keep every site blocked"
                           onClick={() => egressBulk('reject')}>Deny all</Button>
                   <Button variant="ghost" disabled={busy}
                           title="put every site on its project's always-allow list"
                           onClick={() => egressBulk('approve')}>Allow all</Button></>}>
          <Group>
            {pending.map((p) => (
              <HostRequest key={p.id} p={p} onDecide={egressAct} scoped={!!slug}
                           projectName={projLabel(p.project_slug)} />
            ))}
          </Group>
          {picker}
        </Section>
      )}

      {/* ---- security alerts: one card per run (alerts and the agents' own reports) ---- */}
      {runs.length > 0 && (
        <Section title="Security alerts" count={totals.need + totals.reports}
                 actions={!slug && <>
                   {/* ack_all is global: inside a single project's Workspace panel it
                       would silently clear other projects' alerts, so it stays off */}
                   {totals.reports > 0 && (
                     <Button variant="ghost" disabled={busy} title="mark every agent report resolved"
                             onClick={() => ackAllAlerts(true)}>Resolve all reports</Button>)}
                   {totals.need > 0 && (
                     <Button variant="ghost" disabled={busy}
                             title="mark every alert as seen (agent reports stay)"
                             onClick={() => ackAllAlerts(false)}>Acknowledge all</Button>)}
                 </>}>
          <RunCards runs={runs} slug={slug} onChanged={loadAlerts}
                    onOpenBoard={(a) => setBoard({ id: a.id, seed: a })} />
        </Section>
      )}

      {livePages.length > 0 && (
        <Section title="Published pages" count={livePages.length}
                 lede="Live now. Anyone with a link can open it; unpublish to stop the link working.">
          <Group>{livePages.map((p) => <PageLiveCard key={p.id} p={p} onDone={loadBoxReqs} />)}</Group>
        </Section>
      )}

      {board && (
        <SecurityBoard eventId={board.id} seed={board.seed}
                       onClose={() => setBoard(null)} onAck={ackAlert} />
      )}
    </div>
  )
}

// One commit or merge request. The message, branch and summary are the
// agent's: text nodes only. A request whose last approval failed stays
// pending with `error` set; approving it again retries.
function GitRequest({ r, project, showProject, busy, onAct }) {
  const merge = r.kind === 'push'
  const lines = String(r.message || '').split('\n')
  const stat = merge && r.summary ? r.summary.split('\n').pop().trim() : ''
  return (
    <Item
      meta={<>
        <b>{merge ? 'Merge request' : 'Commit'} #{r.id}</b>
        {showProject && <> for {project}</>}
        {merge && r.branch && <> from <span className="mono">{r.branch}</span></>}
        {r.conversation_id && <>, from <Link to={`/c/${r.conversation_id}`}>chat {r.conversation_id}</Link></>}
        {r.created_at && <>, {ago(r.created_at)}</>}
      </>}
      title={lines[0]}
      titleAttr={merge && r.summary ? `${r.message}\n\n${r.branch}\n${r.summary}` : r.message}
      state={r.error && <State tone="bad" why={r.error}>Last attempt failed</State>}
      change={merge
        ? `${stat ? `${stat}. ` : ''}Approve merges the pull request into main.`
        : 'Approve commits the changes to main, then pushes them if the project has a remote.'}
      actions={<>
        {merge && r.pr_url && (
          <a className="ghost-link" href={r.pr_url} target="_blank" rel="noreferrer"
             title={`the diff in Gitea (${r.branch})`}>View diff</a>)}
        <Button variant="ghost" disabled={busy} onClick={() => onAct('reject')}>Reject</Button>
        <Button disabled={busy} onClick={() => onAct('approve')}
                title={merge ? 'merge the pull request into main' : 'commit, then push'}>
          {r.error ? 'Retry' : merge ? 'Approve merge' : 'Approve'}</Button>
      </>} />
  )
}

// ---- the shell ----
// Security (the page once called Review) is a layout route (routes.jsx): this
// is the <h1> and the tab strip, and the tabs are real URLs — /security,
// /security/network, /security/logs, /security/secrets — so each is linkable
// and NavLink lights the current one. The old /review* addresses redirect.
// Network and Logs used to be top-level pages; they are the guest's traffic
// and the agent's transcripts, which is to say evidence, and evidence belongs
// beside the queue that cites it. The old /network and /logs redirect here.
//
// THE CONTRACT for a tab (an <Outlet> child): it renders inside .review-body,
// a full-height flex column that owns the insets and scrolls if the child
// does not. A tab that wants its own inner scrolling (Network's feed, Logs'
// transcript) is `flex: 1; min-height: 0` and never overflows the body; a
// tab that is a plain document (the queue, Secrets) just flows and the body
// scrolls. No tab paints a heading of its own.
export default function Review() {
  const count = useContext(PendingCountContext)
  const { pathname } = useLocation()
  return (
    <Page variant="fill" title="Security" className="review-shell"
          actions={(
            <ScrollHint><Tabs label="Security sections" items={[
              { to: '/security', end: true, label: 'Queue', count },
              { to: '/security/persistent', label: 'Persistent' },
              { to: '/security/network', label: 'Network' },
              { to: '/security/profiles', label: 'Profiles' },
              { to: '/security/logs', label: 'Logs' },
              { to: '/security/secrets', label: 'Secrets' },
            ]} /></ScrollHint>
          )}>
      <div className="review-body">
        {/* one line on what this tab is, in the tab's own column */}
        {ledeFor(SECURITY_LEDES, pathname) && <p className="tab-lede dim">{ledeFor(SECURITY_LEDES, pathname)}</p>}
        <Outlet />
      </div>
    </Page>
  )
}

// The index tab: the queue and its history in the main column; beside it (under
// it on a narrow screen) what is switched on and Auto review's controls.
export function ReviewHome() {
  return (
    <div className="review-page sq-home">
      <div className="sq-main">
        <ReviewQueue />
        <SecurityHistory />
      </div>
      <aside className="sq-rail" aria-label="How the queue is set up">
        <Posture />
        <TriagePanel />
      </aside>
    </div>
  )
}
