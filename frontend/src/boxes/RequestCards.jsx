// The requests an agent files and the operator decides on, as rows of the
// Security queue: a package for an image variant, a service (a long-lived
// process in a service box), an environment, a skill, a held journal line, a
// page to publish. PackageSummary and PackageApprove are also used by the VMs
// page's Catalogue.
//
// Every row reads the same way (securityParts.jsx): who asked and where, what is
// asked, what approving it changes, then the decision, the refusing verb first
// and the approving one last.
//
// Everything in a request (package names, commands, reasons, argv, env, diffs)
// was written by the agent. All of it renders as text nodes.
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Button, Modal, Select } from '../components/index.js'
import { notify, notifyError } from '../notify.js'
import { useAsk } from '../ask.jsx'
import { ago } from '../format.js'
import { approvePackage, listPackages, rejectPackage, resolvePackages } from './api/packages.js'
import { approveService, getService, rejectService } from './api/services.js'
import { approveEnvironment, rejectEnvironment } from './api/environments.js'
import { approveHeldJournal, dropHeldJournal } from './api/journalHeld.js'
import { approvePage, rejectPage, unpublishPage } from './api/pages.js'
import { Copy } from '../copy.jsx'
import { approveSkillRequest, fetchSkillRequest, rejectSkillRequest } from './api/skillRequests.js'
import {
  bytes, diffLines, EXPOSE_LABEL, exposeChoices, exposeDefault, exposePayload, packageReach, PLACEMENTS,
  packagesByManager, skillCardState,
} from './logic.js'
import { Facts, Item, State, Ticks } from '../securityParts.jsx'

// "for website, from chat 42, 3 min ago": the tail of a row's meta sentence
function Where({ project, chat, at }) {
  return (
    <>
      {project && <> for {project}</>}
      {chat && <>, from <Link to={`/c/${chat}`}>chat {chat}</Link></>}
      {at && <>, {ago(at)}</>}
    </>
  )
}

// ---- packages ------------------------------------------------------------------

const pkgVersion = (p) => p.resolved_version || p.version_req || ''

function packageFacts(p) {
  return [
    p.requested_command && ['Agent asked to run',
      <span key="r" className="sq-strike">{p.requested_command}</span>, true],
    ['Host will run', p.canonical_command || 'pinned once the version is resolved', !!p.canonical_command],
    p.integrity && p.resolved_version && ['Integrity', p.integrity, true],
    p.reason && ['Reason', p.reason],
  ]
}

// The request on its own: what is asked and the commands, as a block (the
// approval modal and the Catalogue show it inside their own rows)
export function PackageSummary({ p }) {
  return (
    <div className="sq-item-main">
      <div className="sq-meta">
        <b>{p.manager} package</b>
        <Where project={p.project_slug || p.source || 'operator'} chat={p.conversation_id}
               at={p.created_at} />
      </div>
      <div className="sq-title mono">{p.package}{pkgVersion(p) && ` ${pkgVersion(p)}`}</div>
      {!p.resolved_version && <div className="sq-states"><State tone="warn">Not resolved yet</State></div>}
      <Facts rows={packageFacts(p)} />
    </div>
  )
}

// A queue row: the summary, where it installs, Reject / Approve… (Approve opens
// the modal, which shows every project the variant reaches)
export function PackageRequest({ p, onApprove, onReject }) {
  return (
    <Item meta={<><b>{p.manager} package</b>
                  <Where project={p.project_slug || p.source || 'operator'} chat={p.conversation_id}
                         at={p.created_at} /></>}
          mono title={`${p.package}${pkgVersion(p) ? ` ${pkgVersion(p)}` : ''}`}
          state={!p.resolved_version && (
            <State tone="warn" why="its exact version and integrity are not pinned yet; Approve… can resolve them">
              Not resolved yet</State>)}
          change={p.card ? <Ticks text={p.card} /> : <>Installs into <span className="mono">{p.target_variant}</span></>}
          actions={<>
            <Button variant="ghost" onClick={onReject}>Reject</Button>
            <Button onClick={onApprove}>Approve…</Button>
          </>}>
      <Facts rows={packageFacts(p)} />
    </Item>
  )
}

// The approval card: the variant it installs into, and every project that
// variant reaches (operator decision 0.2) — directly, or through a variant
// built from it. For the row's own target the server's `card` sentence and
// `variant_used_by_detail` are shown as sent.
// Approval needs the dry-run's pinned version and integrity. That dry-run runs
// in the background when the request is filed; when it has not succeeded the
// modal says why and resolves on the spot, instead of an Approve that can only
// answer "not resolved yet" (2026-10-02: a request stuck there for a day).
export function PackageApprove({ p: given, variants, onClose, onDone }) {
  const [p, setP] = useState(given)
  const [target, setTarget] = useState(given?.target_variant || 'main')
  const [ack, setAck] = useState(false)
  const [build, setBuild] = useState(false)
  const [busy, setBusy] = useState(false)
  const [resolving, setResolving] = useState(false)
  useEffect(() => {
    setP(given); setTarget(given?.target_variant || 'main'); setAck(false); setBuild(false)
  }, [given])
  if (!p) return null
  const reach = packageReach(p, target, { variants })
  const names = [...new Set([...(variants || []).map((x) => x.name), p.target_variant].filter(Boolean))]
  const unresolved = !p.resolved_version
  // integrity carries "unresolved: <why>" when a dry-run failed
  const why = unresolved && /^unresolved: /.test(p.integrity || '')
    ? p.integrity.replace(/^unresolved: /, '') : null
  async function resolve() {
    setResolving(true)
    try {
      const r = await resolvePackages()
      const fresh = (await listPackages()).find((x) => x.id === p.id)
      if (fresh) setP(fresh)
      if (r?.error) notifyError(new Error(`resolve: ${r.error}`))
    } catch (e) { notifyError(e) }
    setResolving(false)
  }
  async function go() {
    setBusy(true)
    try {
      const r = await approvePackage(p.id, { targetVariant: target, build })
      if (build) notify(r?.build_started ? `building ${target}` : `approved — the build of ${target} did not start (busy?)`)
      onDone()
    } catch (e) { notifyError(e) }
    setBusy(false)
  }
  const used = reach.all || []
  return (
    <Modal open title={`Approve ${p.package}?`} onClose={onClose} onSubmit={go} width={560}
           footer={<>
             <Button variant="ghost" onClick={onClose}>Cancel</Button>
             <Button type="submit" disabled={!ack || busy || unresolved}>Approve</Button>
           </>}>
      <PackageSummary p={p} />
      {unresolved && (
        <div className="sq-note">
          <p>Approval needs the exact version and its integrity, which a dry-run in a builder box
            pins{why ? <>. The last dry-run failed: <span className="mono">{why}</span></> : '. It has not run, or did not finish.'}</p>
          <Button variant="ghost" disabled={resolving} onClick={resolve}>
            {resolving ? 'Resolving… (about a minute)' : 'Resolve now'}</Button>
        </div>
      )}
      <Select label="Installs into" value={target} onChange={(e) => setTarget(e.target.value)}
              options={names.length ? names : [target]} />
      {target === p.target_variant && p.card
        ? <p className="sq-change"><Ticks text={p.card} /></p>
        : <p className="sq-change">Installs into <span className="mono">{target}</span>, used
            by {used.length ? used.join(', ') : 'no project yet'}.</p>}
      {reach.via.length > 0 && (
        <p className="sq-change">
          {reach.via.map(([v, slugs]) => (
            <span key={v}>Through <span className="mono">{v}</span>: {slugs.join(', ')}. </span>))}
        </p>)}
      <p className="sq-change">Every project on <span className="mono">{target}</span> gets it at the
        variant's next version. The host runs the "Host will run" command, never the agent's.</p>
      <label className="sq-check">
        <input type="checkbox" checked={build} onChange={(e) => setBuild(e.target.checked)} />
        <span>Build a new version of <span className="mono">{target}</span> now</span>
      </label>
      <label className="sq-check">
        <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
        <span>I have checked the package name, version and integrity</span>
      </label>
    </Modal>
  )
}

export function usePackageReject(onDone) {
  const ask = useAsk()
  return async (p) => {
    const reason = await ask.prompt(`Reject ${p.package}? Reason (optional)`, '', { confirmLabel: 'Reject' })
    if (reason === null) return
    try { await rejectPackage(p.id, reason); onDone() } catch (e) { notifyError(e) }
  }
}

// ---- services ------------------------------------------------------------------

// The service request, in full: the definition, what changed since the
// approved version, the ports and where each is exposed, and the REQUIRED
// placement choice (preselected from the project's profile, changeable here,
// stored on the service row). `lan` is {ip, configured, error} from the
// list response.
export function ServiceRequest({ s, lan, profilePlacement, onDone }) {
  const lanIp = lan?.ip || ''
  const [full, setFull] = useState(null)
  const [placement, setPlacement] = useState(s.placement || profilePlacement || '')
  const [expose, setExpose] = useState(() => Object.fromEntries((s.ports || []).map((p) =>
    [p.port, exposeDefault(p.expose, lanIp)])))
  const [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const ask = useAsk()
  useEffect(() => {
    getService(s.id).then(setFull).catch(() => setFull({ diff: null }))
  }, [s.id])

  async function approve() {
    setBusy(true)
    try {
      await approveService(s.id, { placement, exposePorts: exposePayload(expose, s.ports, lanIp) })
      onDone()
    } catch (e) { notifyError(e) }
    setBusy(false)
  }
  async function reject() {
    const reason = await ask.prompt(`Reject service ${s.name}? Reason (optional)`, '',
                                    { confirmLabel: 'Reject' })
    if (reason === null) return
    try { await rejectService(s.id, reason); onDone() } catch (e) { notifyError(e) }
  }

  const env = Object.entries(s.env || {})
  const diff = diffLines(full?.diff)
  return (
    <Item below
          meta={<><b>{s.supersedes_id ? `Service update (replaces #${s.supersedes_id})` : 'Service'}</b>
                  <Where project={s.project_slug} chat={s.conversation_id} at={s.created_at} /></>}
          mono title={s.name}
          change={s.description}
          actions={<>
            <Button variant="ghost" onClick={reject}>Reject</Button>
            <Button disabled={!ack || !placement || busy} onClick={approve}>Approve service</Button>
          </>}>
      <Facts rows={[
        ['Command', (s.command || []).map((a) => JSON.stringify(a)).join(' '), true],
        ['Working directory', s.workdir || '.', true],
        ['Files', (s.files || []).join('  ') || 'none', true],
        ['Restart', s.restart],
        ['Can reach', (s.egress_hosts || []).join('  ') || 'nothing (every site denied)', !!(s.egress_hosts || []).length],
        env.length > 0 && ['Environment', env.map(([k, v]) => `${k}=${v}`).join('  '), true],
        ['Content sha256', s.artifact_sha256, true],
        ['Reason', s.reason],
      ]} />

      {s.supersedes_id && (
        <details className="sq-form" open>
          <summary className="sq-form-h">Changes since approved #{s.supersedes_id}</summary>
          {!full && <p className="sq-change">Loading…</p>}
          {full && !diff.length && <p className="sq-change">No file changes (the definition only).</p>}
          {diff.length > 0 && (
            <pre className="sq-pre">{diff.map((l, i) => (
              <span key={i} className={l.cls === 'add' ? 'd-add' : l.cls === 'del' ? 'd-del'
                : l.cls === 'hunk' ? 'd-hunk' : undefined}>{l.text}{'\n'}</span>))}</pre>
          )}
        </details>
      )}

      {(s.ports || []).length > 0 && (
        <div className="sq-form">
          <div className="sq-form-h">Ports <span>nothing is reachable unless you expose it</span></div>
          {s.ports.map((p) => (
            <div className="sq-port" key={p.port}>
              <span className="mono">{p.port}/{p.protocol || 'tcp'}</span>
              <span className="sq-port-what">{p.purpose}{p.expose && p.expose !== 'none'
                ? `, asked for ${p.expose}` : ''}</span>
              <Select aria-label={`expose port ${p.port}`} value={expose[p.port] || 'none'}
                      onChange={(e) => setExpose((x) => ({ ...x, [p.port]: e.target.value }))}
                      options={exposeChoices(p.expose, lanIp)
                        .map((v) => ({ value: v, label: EXPOSE_LABEL[v] }))} />
            </div>
          ))}
          {!lanIp && (s.ports || []).some((p) => p.expose === 'lan') && (
            <p className="sq-change">LAN exposure is unavailable: {lan?.configured
              ? <>the configured address <span className="mono">{lan.configured}</span> was refused
                  {lan.error ? <> ({lan.error})</> : null}.</>
              : <>no services LAN address is configured (<span className="mono">services_lan_ip</span>).</>}</p>)}
          {lanIp && Object.values(expose).includes('lan') && (
            <State tone="warn">LAN ports bind on {lanIp} only, never on CNTRL's own address</State>)}
        </div>
      )}

      <fieldset className="sq-form sq-fieldset">
        <legend className="sq-form-h">Placement <span>(required
          {profilePlacement ? `; the profile says ${profilePlacement.replace('_', ' ')}` : ''})</span></legend>
        {PLACEMENTS.map((o) => (
          <label key={o.value} className="sq-radio">
            <input type="radio" name={`place-${s.id}`} value={o.value}
                   checked={placement === o.value} onChange={() => setPlacement(o.value)} />
            <span>{o.label} <span>{o.hint.replace(/ — /g, ': ')}</span></span>
          </label>
        ))}
      </fieldset>

      <label className="sq-check sq-readit">
        <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
        <span>I have read the definition{s.supersedes_id ? ' and the changes' : ''}; run exactly this
          (content <span className="mono">{String(s.artifact_sha256 || '').slice(0, 12)}</span>)</span>
      </label>
    </Item>
  )
}

// ---- environment requests ---------------------------------------------------------

// A new image variant plus the packages it adds, filed by an agent for its project.
// Approve builds the image and moves the project onto it; nothing else changes.
export function EnvironmentCard({ e, onDone }) {
  const [busy, setBusy] = useState(false)
  const [resolving, setResolving] = useState(false)
  const ask = useAsk()
  const pending = e.status === 'pending'
  const use = e.kind === 'use'   // move onto an existing image: nothing built
  async function resolve() {
    setResolving(true)
    try {
      const r = await resolvePackages()
      if (r?.error) notifyError(new Error(`resolve: ${r.error}`))
      onDone()
    } catch (err) { notifyError(err) }
    setResolving(false)
  }
  async function approve() {
    const ok = await ask.confirm(use
      ? `Approve moving ${e.project_slug} onto image ${e.name}? Nothing is built or installed; ${e.plan || ''}`
      : `Approve environment ${e.name}? The host builds the image from ${e.from_variant} with the `
      + `pinned packages shown, then ${e.project_slug} boots on it.`,
      { confirmLabel: use ? 'Approve' : 'Approve & build' })
    if (!ok) return
    setBusy(true)
    try {
      const r = await approveEnvironment(e.id, { build: !use })
      if (use) notify(`${e.project_slug} moves onto ${e.name} at its next box boot`)
      else notify(r?.build_started ? `building ${e.name}` : `approved — the build of ${e.name} did not start (busy?)`)
      onDone()
    } catch (err) { notifyError(err) }
    setBusy(false)
  }
  async function reject() {
    const reason = await ask.prompt(`Reject environment ${e.name}? Reason (optional)`, '',
                                    { confirmLabel: 'Reject' })
    if (reason === null) return
    try { await rejectEnvironment(e.id, reason); onDone() } catch (err) { notifyError(err) }
  }
  const pkgRows = use ? [] : packagesByManager(e.packages).map(([m, list]) => [
    `Adds (${m})`,
    list.map((p) => (
      <div key={p.id ?? p.package} className="sq-pkgline">
        <span className="mono">{p.package}{p.version_req ? ` ${p.version_req}` : ''}</span>
        {p.resolved_version
          ? <> pinned to <span className="mono">{p.resolved_version}</span></>
          : <span className="sq-warn-text"> not resolved yet</span>}
        {p.integrity && p.resolved_version && <span className="sq-sub mono">{p.integrity}</span>}
      </div>)),
  ])
  return (
    <Item below
          meta={<><b>{use ? 'Image change' : 'New environment'}</b>
                  <Where project={e.project_slug} chat={e.conversation_id} at={e.created_at} /></>}
          mono title={e.name}
          state={pending && !use && !e.resolved && (
            <State tone="warn" why="approval needs every package's exact version and integrity, which a dry-run in a builder box pins">
              Not resolved yet</State>)}
          change={use ? (e.summary || `Moves ${e.project_slug} onto image ${e.name}.`)
            : `A new image built from ${e.from_variant || 'main'}; ${e.project_slug} boots on it. Other projects are unchanged.`}
          actions={pending && <>
            {!use && !e.resolved && (
              <Button variant="ghost" disabled={resolving} onClick={resolve}>
                {resolving ? 'Resolving… (about a minute)' : 'Resolve now'}</Button>)}
            <span className="sq-spacer" />
            <Button variant="ghost" onClick={reject}>Reject</Button>
            <Button disabled={!e.resolved || busy} onClick={approve}>{use ? 'Approve' : 'Approve and build'}</Button>
          </>}>
      <Facts rows={[
        ...pkgRows,
        !use && ['Memory floor', e.min_mem_mb ? `${e.min_mem_mb} MB` : `as ${e.from_variant || 'main'}`],
        ['Reason', e.reason],
        pending && e.plan && ['On approve', <Ticks key="plan" text={e.plan} />],
        !pending && ['Status', `${e.status}${e.decided_by ? ` by ${e.decided_by}` : ''}`],
        e.profile && ['Profile', <>{e.project_slug} now uses profile <span className="mono">{e.profile.name}</span></>],
        e.build && ['Build', `${e.build.version}, ${e.build.status}${e.build.error ? `: ${e.build.error}` : ''}`],
      ]} />
    </Item>
  )
}

// ---- skill install requests -------------------------------------------------------

// The source, reason and every fetched word are agent- or internet-written.
// Pending: only the source is shown and nothing has been contacted. Fetch makes the
// host download and vet it; Install & grant sends the hash of the text read here.
export function SkillRequestCard({ r, onDone }) {
  const [busy, setBusy] = useState(false)
  const ask = useAsk()
  const st = skillCardState(r)
  async function fetchIt() {
    setBusy(true)
    try { await fetchSkillRequest(r.id) } catch (err) { notifyError(err) }
    setBusy(false)
    onDone()
  }
  async function install() {
    const ok = await ask.confirm(
      `Install skill ${r.skill_name || r.source}? You have read SKILL.md (sha256 ${String(r.skill_sha256).slice(0, 12)}). `
      + 'It is installed exactly as read and granted to agents if its requirements are met.',
      { confirmLabel: 'Install & grant' })
    if (!ok) return
    setBusy(true)
    try {
      const done = await approveSkillRequest(r.id, r.skill_sha256)
      notify(done?.granted ? 'skill installed and granted' : `skill installed, not granted${done?.error ? `: ${done.error}` : ''}`)
      onDone()
    } catch (err) { notifyError(err) }
    setBusy(false)
  }
  async function reject() {
    const reason = await ask.prompt(`Reject skill request ${r.source}? Reason (optional)`, '',
                                    { confirmLabel: 'Reject' })
    if (reason === null) return
    try { await rejectSkillRequest(r.id, reason); onDone() } catch (err) { notifyError(err) }
  }
  const change = st.status === 'pending'
    ? 'Nothing has been downloaded yet. Fetch makes the host download and check it, so you can read it before installing.'
    : st.status === 'fetched' ? 'Read SKILL.md below. Install puts in exactly this text and grants it to agents if its requirements are met.'
      : null
  return (
    <Item below
          meta={<><b>Skill {st.status === 'fetched' ? 'install, fetched' : 'install'}</b>
                  <Where project={r.project_slug} chat={r.conversation_id} at={r.created_at} /></>}
          mono title={r.skill_name || r.name || r.source}
          state={<>
            {r.error && st.status !== 'installed' && <State tone="bad" why={r.error}>Error</State>}
            {(r.flags || []).map((f, i) => (
              <State key={i} tone="warn" why={`${f.file}: ${f.trigger}`}>Diff gate</State>))}
            {(r.requirements || []).filter((q) => !q.met).map((q, i) => (
              <State key={`q${i}`} tone="warn" why={q.reason}>Requirement not met</State>))}
          </>}
          change={change}
          actions={(st.canFetch || st.canRefetch || st.canReject) && <>
            {st.canReject && <Button variant="ghost" onClick={reject}>Reject</Button>}
            {st.canRefetch && <Button variant="ghost" disabled={busy} onClick={fetchIt}>Fetch again</Button>}
            {st.canFetch && <Button disabled={busy} onClick={fetchIt}>Fetch and read</Button>}
            {st.canInstall && <Button disabled={busy} onClick={install}>Install and grant</Button>}
          </>}>
      <Facts rows={[
        (r.skill_name || r.name) && ['Source', r.source, true],
        ['Reason', r.reason],
        r.ref && ['Ref', r.ref, true],
        r.description && ['Description', r.description],
        st.files.length > 0 && ['Files', st.files.join('  '), true],
        (r.requirements || []).length > 0 && ['Requires', r.requirements.map((q) => `${q.met ? 'met' : 'not met'}: ${q.reason}`).join('; ')],
        st.status === 'installed' && ['Installed', `${r.granted ? 'granted to agents' : 'installed, not granted'}${st.notGranted && r.error ? `: ${r.error}` : ''}`],
      ]} />
      {r.skill_md && st.status === 'fetched' && <pre className="sq-pre">{r.skill_md}</pre>}
    </Item>
  )
}

// ---- journal lines held as [unverified] -----------------------------------------
// The line was written by an agent in a turn that had read untrusted content. It
// stays in project.md but out of every prompt until you approve it (the tag comes
// off, nothing else in the file changes) or drop it (the line is removed).
export function JournalHeldCard({ h, onDone }) {
  const [busy, setBusy] = useState(false)
  async function decide(fn, done) {
    setBusy(true)
    try { await fn(h); notify(done) } catch (err) { notifyError(err) }
    setBusy(false)
    onDone()
  }
  return (
    <Item meta={<><b>Journal entry</b> in {h.project}{h.date && <>, dated {h.date}</>}</>}
          title={h.text}
          change="An agent wrote it after reading untrusted content, so it stays out of every prompt until you approve it. Drop removes the line."
          actions={<>
            <Button variant="ghost" disabled={busy}
                    onClick={() => decide(dropHeldJournal, 'journal entry dropped')}>Drop</Button>
            <Button disabled={busy}
                    onClick={() => decide(approveHeldJournal, 'journal entry approved')}>Approve</Button>
          </>} />
  )
}

// ---- published pages -----------------------------------------------------------

function PageLink({ url }) {
  return (
    <div className="sq-link">
      <a href={url} target="_blank" rel="noopener noreferrer">{url}</a>
      <Copy text={url} label="Copy link" />
    </div>
  )
}

// An agent asked to publish a file or folder of a project: a copy is served at its own
// link, and anyone with the link can open it (scripts in it run, in a sandbox). The
// names and paths were written by the agent, so they render as text. After Approve the
// row stays and shows the link; `onDone` refreshes the lists.
export function PagePublishCard({ p, onDone }) {
  const [busy, setBusy] = useState(false)
  const [url, setUrl] = useState(null)
  const [all, setAll] = useState(false)
  const files = p.files || []
  const shown = all ? files : files.slice(0, 5)
  async function approve() {
    setBusy(true)
    try {
      const r = await approvePage(p)
      setUrl(r?.url || '')
      notify(`published ${p.name}`)
    } catch (err) { notifyError(err) }
    setBusy(false)
    onDone()
  }
  async function reject() {
    setBusy(true)
    try { await rejectPage(p); notify('publish request rejected') } catch (err) { notifyError(err) }
    setBusy(false)
    onDone()
  }
  return (
    <Item meta={<><b>Page to publish</b> from {p.project}<Where chat={p.conversation_id} />, {bytes(p.bytes)}</>}
          title={p.name}
          state={url !== null && <State tone="ok">Published</State>}
          change={url === null ? 'Anyone with the link can open it, and scripts in it run.' : null}
          actions={url === null && <>
            <Button variant="ghost" disabled={busy} onClick={reject}>Reject</Button>
            <Button disabled={busy} onClick={approve}>Approve</Button>
          </>}>
      <Facts rows={[
        ['Path', p.path, true],
        ['Files', <>
          {shown.map((f) => (
            <span key={f.path} className="sq-sub mono">{f.path} {bytes(f.size)}</span>))}
          {files.length > shown.length && (
            <button type="button" className="sq-fold" onClick={() => setAll(true)}>
              Show all {files.length}</button>)}
        </>],
      ]} />
      {url ? <PageLink url={url} /> : null}
    </Item>
  )
}

// A live page: its link, a copy button, and Unpublish (the link stops working).
export function PageLiveCard({ p, onDone }) {
  const ask = useAsk()
  const [busy, setBusy] = useState(false)
  async function unpublish() {
    const ok = await ask.confirm(
      `Unpublish ${p.name}? Its link stops working. The project's files are not touched.`,
      { confirmLabel: 'Unpublish', danger: true })
    if (!ok) return
    setBusy(true)
    try { await unpublishPage(p); notify(`unpublished ${p.name}`) } catch (err) { notifyError(err) }
    setBusy(false)
    onDone()
  }
  return (
    <Item meta={<><b>Page</b> from {p.project}</>}
          title={p.name}
          state={<State tone="ok">Live</State>}
          actions={<Button variant="ghost" danger disabled={busy} onClick={unpublish}>Unpublish</Button>}>
      {p.url && <PageLink url={p.url} />}
    </Item>
  )
}
