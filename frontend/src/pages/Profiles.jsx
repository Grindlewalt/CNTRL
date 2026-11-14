import { useEffect, useMemo, useState } from 'react'
import { Button, Checkbox, EmptyState, HelpTip, Input, Modal, Select, Tag, useRowMenu } from '../components/index.js'
import './runtime.css'
import { api } from '../api.js'
import { notify, notifyError } from '../notify.js'
import { useAsk } from '../ask.jsx'
import {
  assignProfile, createProfile, deleteProfile, listProfiles, makeDefaultProfile, secretChoices,
  updateProfile,
} from '../boxes/api/profiles.js'
import { listImages } from '../boxes/api/images.js'
import { listBoxes } from '../boxes/api/vms.js'
import { listProjects } from '../boxes/api/persist.js'
import {
  assignableProjects, blankProfile, deleteBlock, networkMode, newSitesText, parseHosts, profilePayload,
  RUNS_IN, runsIn, runsInText, validateProfile, withNetworkMode, withRunsIn,
} from '../boxes/logic.js'
import { LoadError, ProjectList, RuntimeStatus, Unavailable, useLoad } from '../boxes/ui.jsx'
import { secretChecklist } from '../securityCopy.js'
import SecretModal from './SecretModal.jsx'

// Security > Profiles: a project's whole security posture in one named row —
// which secrets it may have, how its egress is judged, whether its alerts are
// auto-handled, and the box it runs in. Every profile can be renamed, edited
// and deleted, except the default (the one new and unassigned projects use;
// "Make default" moves the mark) and one a project still uses. Every project
// has exactly one profile. First-run setup creates the first default.
//
// The allow and block lists belong to the profile (projects share profiles).
// Edit opens a Modal. The explanations sit behind HelpTips; the one line that
// stays on screen is what happens to a site on neither list.

const NEVER_AUTO = 'service_* and svc_unreported, '
  + 'package_* and image_variant_built, unexpected_process and proc_report_mismatch, '
  + 'profile_changed and profiles_migrated, docker_weak_isolation / docker_hardening_refused / '
  + 'docker_socket_refused, persist_imported and persist_disk_deleted, plus egress_anomaly, '
  + 'host_cut and secret_leak.'

// a new profile asks for packages and services (each request still waits for you)
const newProfile = () => ({ ...blankProfile(), allow_services: true, allow_package_requests: true })

const OUTCOME = {
  ask: 'A site on neither list waits for you.',
  allow: 'A site on neither list is let through.',
}

export default function Profiles() {
  const pr = useLoad(listProfiles)
  const [editing, setEditing] = useState(null)       // a profile object (id null = new)
  const [variants, setVariants] = useState(['main', 'dev', 'desktop'])
  const [projects, setProjects] = useState([])
  const [runtimes, setRuntimes] = useState(null)
  const [budget, setBudget] = useState(null)
  const ask = useAsk()

  useEffect(() => {
    listImages().then((r) => { if (r.variants.length) setVariants(r.variants.map((v) => v.name)) })
      .catch(() => {})
    listProjects().then((ps) => setProjects(assignableProjects(ps))).catch(() => {})
    listBoxes().then((r) => { setRuntimes(r.runtimes); setBudget(r.budget || null) }).catch(() => {})
  }, [])

  async function remove(p) {
    if (!await ask.confirm(`Delete profile ${p.name}?`, {
      body: 'No project uses it. This cannot be undone.',
      confirmLabel: 'Delete', danger: true })) return
    try { await deleteProfile(p.id); pr.reload() } catch (e) { notifyError(e) }
  }

  async function makeDefault(p) {
    if (!await ask.confirm(`Make ${p.name} the default?`, {
      body: 'New projects, and every project without a profile of its own, will use it '
        + `from their next turn: new sites ${newSitesText(p)}; default box ${runsInText(p)} (projects that picked their own keep it).`,
      confirmLabel: 'Make default', danger: p.default_verdict === 'allow' && !p.network_off })) return
    try { await makeDefaultProfile(p.id); notify(`${p.name} is now the default`); pr.reload() }
    catch (e) { notifyError(e) }
  }

  if (pr.unavailable) return <div className="bx-page"><Unavailable what="Security profiles" /></div>
  const list = pr.data || []
  return (
    <div className="bx-page">
      <LoadError error={pr.error} />
      <section className="sbx-sec">
        <div className="sbx-sec-head"><h3>Profiles</h3>
          <span className="sec-count">{list.length}</span>
          <HelpTip label="About profiles">
            A profile is a set of rules a project runs under: its network, secrets, box and what
            the agent may request. Every project has exactly one; projects on the same profile
            share its always-allow and always-block lists.</HelpTip>
          <div className="sec-actions">
            <Button variant="ghost" onClick={() => setEditing(newProfile())}>New profile</Button>
          </div>
        </div>
        {!pr.data && !pr.error && <div className="dim">…</div>}
        {pr.data && list.length === 0 && <EmptyState>no profiles yet: the first project creates the default</EmptyState>}
        <ul className="list-divided pf-list">
          {list.map((p) => (
            <ProfileRow key={p.id} p={p} active={editing?.id === p.id}
                        onEdit={() => setEditing({ ...p })}
                        onDuplicate={() => setEditing({
                          ...p, id: null, is_default: false, name: `${p.name} copy`, projects: [] })}
                        onDefault={() => makeDefault(p)} onDelete={() => remove(p)} />
          ))}
        </ul>
      </section>

      {editing && (
        <ProfileModal key={editing.id ?? 'new'} initial={editing} variants={variants}
                      runtimes={runtimes} budget={budget}
                      names={list.filter((p) => p.id !== editing.id).map((p) => p.name)}
                      onClose={() => setEditing(null)}
                      onSaved={() => { setEditing(null); pr.reload() }} />
      )}

      <Assignments profiles={list} projects={projects} onDone={pr.reload} />
    </div>
  )
}

function ProfileRow({ p, active, onEdit, onDuplicate, onDefault, onDelete }) {
  const block = deleteBlock(p)
  const { rowProps, menu, open } = useRowMenu([
    { label: 'Edit', onSelect: onEdit },
    { label: 'Duplicate', onSelect: onDuplicate },
    !p.is_default && { label: 'Make default', onSelect: onDefault },
    '-',
    { label: 'Delete', danger: true, disabled: !!block, sub: block || undefined, onSelect: onDelete },
  ], { label: `Actions for ${p.name}` })
  const net = networkMode(p)
  const secrets = (p.secrets || []).length
  return (
    <li {...rowProps} className={`pf-row${active || open ? ' menu-open' : ''}`}>
      <button type="button" className="pf-row-main" onClick={onEdit}>
        <span className="pf-name"><b>{p.name}</b> {p.is_default && <Tag>default</Tag>}</span>
        <span className="pf-sum">
          {net === 'off' ? 'Network off' : `Network on, new sites ${newSitesText(p)}`}
          {net !== 'off' && `, ${(p.allow_hosts || []).length} allowed, ${(p.deny_hosts || []).length} blocked`}
          {`; ${runsInText(p)}; ${secrets} secret${secrets === 1 ? '' : 's'}`}
        </span>
        <span className="pf-used"><ProjectList slugs={p.projects} empty="no project" /></span>
      </button>
      {menu}
    </li>
  )
}

function ProfileModal({ initial, variants, runtimes, budget, names, onClose, onSaved }) {
  const [p, setP] = useState({
    ...initial,
    // an old row may carry neither: the same defaults a new profile gets
    service_placement: initial.service_placement || 'per_project',
    box_runtime: initial.box_runtime || 'kvm',
  })
  const [allowText, setAllowText] = useState((initial.allow_hosts || []).join('\n'))
  const [denyText, setDenyText] = useState((initial.deny_hosts || []).join('\n'))
  const [busy, setBusy] = useState(false)
  const [tried, setTried] = useState(false)
  const [picking, setPicking] = useState(false)
  const isNew = initial.id == null
  const set = (k, v) => setP((x) => ({ ...x, [k]: v }))
  const patch = (o) => setP((x) => ({ ...x, ...o }))
  const full = { ...p, allow_hosts: parseHosts(allowText), deny_hosts: parseHosts(denyText) }
  const errs = validateProfile(full, { names })
  const ok = Object.keys(errs).length === 0
  const show = (k) => (tried || k === 'hosts' ? errs[k] : null)
  const net = networkMode(p)
  const on = net !== 'off'
  const where = runsIn(p)
  const held = p.secrets || []

  async function save() {
    setTried(true)
    if (!ok) return
    setBusy(true)
    try {
      const body = profilePayload(full)
      if (isNew) await createProfile(body)
      else await updateProfile(p.id, body)
      notify(`profile ${body.name} saved`)
      onSaved()
    } catch (err) { notifyError(err) }
    setBusy(false)
  }

  // why a "Runs in" choice cannot be picked on this server (null = it can)
  const unavailable = (v) => {
    if (!runtimes || v === 'shared') return null
    const rt = v === 'container' ? runtimes.docker : runtimes.kvm
    return rt && !rt.available ? (rt.reason || 'not available on this server') : null
  }
  const runOptions = RUNS_IN.map((o) => {
    const why = unavailable(o.value)
    return { value: o.value, label: why && where !== o.value ? `${o.label} (unavailable)` : o.label,
      disabled: !!why && where !== o.value }
  })

  return (
    <Modal title={isNew ? 'New profile' : `Edit ${initial.name}`} width={560} onClose={onClose}
           onSubmit={save} submitLabel="Save profile" busy={busy} closeOnScrim={false}
           footerStart={tried && !ok ? <span className="error small">fix the fields marked above</span> : null}>
      <Input label="Name" value={p.name} error={show('name')} autoFocus
             onChange={(e) => set('name', e.target.value)} />

      <div className="field">
        <span>Network <HelpTip label="About network">
          Off means no network at all, whatever the lists say. On lets you keep always-allow and
          always-block lists; a block wins over an allow.</HelpTip></span>
        <div className="pf-seg" role="radiogroup" aria-label="Network">
          {[['on', 'On'], ['off', 'Off']].map(([v, label]) => (
            <button key={v} type="button" role="radio" aria-checked={(v === 'on') === on}
                    className={(v === 'on') === on ? 'on' : ''}
                    onClick={() => patch({ network_off: v === 'off' })}>
              {label}</button>
          ))}
        </div>
        {!on && <span className="field-hint">The network is off: nothing gets out.</span>}
      </div>

      {on && (
        <>
          <div className="field">
            <Select label="New sites" value={net}
                    onChange={(e) => patch(withNetworkMode(e.target.value))}
                    options={[{ value: 'ask', label: 'Ask me' }, { value: 'allow', label: 'Let through' }]} />
            <span className="field-hint">{OUTCOME[net]}</span>
          </div>
          <div className="pf-two">
            <Input textarea label="Always allow (one site per line)" rows={4} spellCheck={false}
                   value={allowText} onChange={(e) => setAllowText(e.target.value)} />
            <Input textarea label="Always block (wins)" rows={4} spellCheck={false}
                   value={denyText} onChange={(e) => setDenyText(e.target.value)} />
          </div>
          {show('hosts') && <span className="error small">{errs.hosts}</span>}
        </>
      )}

      <div className="field">
        <span>Where it runs <HelpTip label="About boxes">
          The box is where the agent's code runs. The shared box is one machine every project
          using it shares. An own VM is a virtual machine per project with its own kernel. An
          own container is a container per project that shares the host's kernel, so it is
          less isolated. A project can pick its own under Network, then Runs in.</HelpTip></span>
        <Select aria-label="Where it runs" value={where} onChange={(e) => patch(withRunsIn(e.target.value))} options={runOptions} />
        {where === 'container' && runtimes && <RuntimeStatus runtimes={runtimes} compact />}
        {show('box_runtime') && <span className="error small">{errs.box_runtime}</span>}
      </div>
      {where !== 'shared' && (
        <div className="pf-two">
          <Select label="Image" value={p.box_image || 'main'}
                  onChange={(e) => set('box_image', e.target.value)}
                  options={[...new Set([...variants, p.box_image || 'main'])]} />
          <div className="field">
            <span>Memory (MB) <HelpTip label="About memory">
              {budget?.ram_mb_cap ? `Every box together may use about ${budget.ram_mb_cap} MB` : 'Per box'}
              {budget?.host_ram_mb ? ` (this host has ${budget.host_ram_mb} MB)` : ''}.
              The desktop image needs at least 1280 MB.</HelpTip></span>
            <Input aria-label="Memory in MB" type="number" min={256} step={64} value={p.box_mem_mb ?? ''}
                   error={show('box_mem_mb')} onChange={(e) => set('box_mem_mb', e.target.value)} />
          </div>
        </div>
      )}

      <div className="field">
        <span>The agent may request <HelpTip label="About requests">
          A request only asks: nothing is installed or started until you approve it.</HelpTip></span>
        <Checkbox label="Packages" checked={!!p.allow_package_requests}
                  onChange={(e) => set('allow_package_requests', e.target.checked)} />
        <Checkbox label="Services" checked={!!p.allow_services}
                  onChange={(e) => set('allow_services', e.target.checked)} />
      </div>

      <div className="field">
        <span>Secrets <HelpTip label="About secrets">
          Names only; values never leave the host. A granted secret is added to requests this
          profile's boxes send to the sites the secret is bound to.</HelpTip></span>
        <div className="pf-secret-line">
          <span className="mono small">{held.length ? held.join(', ') : 'none'}</span>
          <Button variant="ghost" onClick={() => setPicking(true)}>Choose secrets</Button>
        </div>
      </div>

      <Checkbox label="Let Auto review handle this profile's alerts and site requests"
                checked={!!p.auto_handle} onChange={(e) => set('auto_handle', e.target.checked)} />
      <span className="field-hint pf-never">Auto review never handles <HelpTip label="Which alerts">
        {NEVER_AUTO}</HelpTip></span>

      {picking && (
        <SecretPicker held={held} onClose={() => setPicking(false)}
                      onDone={(names2) => { set('secrets', names2); setPicking(false) }} />)}
    </Modal>
  )
}

// The secrets a profile may hold, in their own dialog. Ticking only grants
// the name; a secret itself (value, sites) is edited here too, in its own
// dialog, and a new one can be added.
function SecretPicker({ held, onClose, onDone }) {
  const [choices, setChoices] = useState([])
  const [full, setFull] = useState([])
  const [sel, setSel] = useState(held)
  const [showInfra, setShowInfra] = useState(false)
  const [editing, setEditing] = useState(undefined)    // undefined closed, null new, or a secret
  const load = () => {
    secretChoices().then(setChoices).catch(() => {})
    api('/api/secrets').then((r) => setFull(r.secrets || [])).catch(() => {})
  }
  useEffect(load, [])
  const { list, infra, hidden } = secretChecklist(choices, sel, showInfra)
  const byName = Object.fromEntries(full.map((s) => [s.name, s]))
  return (
    <Modal title="Secrets for this profile" onClose={onClose} onSubmit={() => onDone(sel)}
           submitLabel="Done" closeOnScrim={false}
           help="Ticking grants the name only; the value stays on the host.">
      {list.length === 0 && <span className="dim small">no secrets stored</span>}
      <ul className="list-divided pf-secrets">
        {list.map((s) => (
          <li key={s} className="pf-secret">
            <Checkbox label={<span className="mono small">{s}</span>} checked={sel.includes(s)}
                      onChange={(e) => setSel(e.target.checked ? [...sel, s] : sel.filter((x) => x !== s))} />
            {infra.has(s) && (
              <Tag tone="pending" title="opens CNTRL's own infrastructure, not a third-party service">
                infrastructure</Tag>)}
            {byName[s] && (
              <Button variant="ghost" onClick={() => setEditing(byName[s])}>Edit</Button>)}
          </li>
        ))}
      </ul>
      <div className="pf-secret-line">
        <Button variant="ghost" onClick={() => setEditing(null)}>Add a secret</Button>
        {(hidden > 0 || showInfra) && (
          <Checkbox label={`Show infrastructure secrets${hidden > 0 ? ` (${hidden})` : ''}`}
                    checked={showInfra} onChange={(e) => setShowInfra(e.target.checked)} />)}
      </div>
      {editing !== undefined && (
        <SecretModal secret={editing} onClose={() => setEditing(undefined)} onSaved={load} />)}
    </Modal>
  )
}

function Assignments({ profiles, projects, onDone }) {
  const ask = useAsk()
  const of = useMemo(() => {
    const m = {}
    for (const p of profiles) for (const s of p.projects || []) m[s] = p
    return m
  }, [profiles])
  const def = profiles.find((p) => p.is_default)
  if (!projects.length || !profiles.length) return null
  async function change(slug, id) {
    const to = profiles.find((p) => String(p.id) === String(id))
    if (!to) return
    if (!await ask.confirm(`Move ${slug} to ${to.name}?`, {
      body: `From its next turn: secrets ${(to.secrets || []).join(', ') || 'none'}; `
        + `new sites: ${to.default_verdict === 'allow' && !to.network_off ? 'ALLOWED' : newSitesText(to)}; `
        + `default box: ${runsInText(to)} (unless it picked its own under Runs in).`,
      confirmLabel: 'Move', danger: to.default_verdict === 'allow' })) return
    try { await assignProfile(slug, to.id); onDone() } catch (e) { notifyError(e) }
  }
  return (
    <section className="sbx-sec">
      <div className="sbx-sec-head"><h3>Projects</h3>
        <HelpTip label="About projects">Which profile each project runs under. A project can pick
          its own box under Network, then Runs in.</HelpTip></div>
      <ul className="list-divided pf-list">
        {projects.map((pj) => {
          const cur = of[pj.slug] || def
          return (
            <li key={pj.slug} className="pf-assign">
              <span className="grow">{pj.name} <span className="dim small mono">{pj.slug}</span></span>
              <Select aria-label={`profile for ${pj.slug}`} value={cur ? String(cur.id) : ''}
                      onChange={(e) => change(pj.slug, e.target.value)}
                      options={profiles.map((p) => ({ value: String(p.id), label: p.name }))} />
            </li>
          )
        })}
      </ul>
    </section>
  )
}
