import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api.js'
import { useAsk } from '../../ask.jsx'
import { HelpTip, Tag } from '../../components/index.js'
import { UiGlyph } from '../../shell/glyphs.jsx'
import { tsShort } from '../../format.js'
import { applyPermissionMode, MODE_SHORT } from '../../AskUser.jsx'
import { MODE_HINT, PERMISSION_MODES } from '../../askUser.js'
import { listBoxes } from '../../boxes/api/vms.js'
import { getPlacement } from '../../boxes/api/placement.js'
import { getPolicy } from '../../boxes/api/policy.js'

// The chat's security, in three short sections: how it asks (the permission
// mode, the same rules as the toolbar had: yolo confirms and needs a password
// login), where it runs (the shared VM or the project's own box, its image and
// state) and its network (the profile in effect, on or off, what a new site
// does, the last few decisions for this scope). Every line is read from the
// server; nothing here is decided in the browser.

const VERDICT = {
  allow: 'allowed', deny: 'blocked', auto_allow: 'allowed', auto_deny: 'blocked',
  cut: 'cut', approved: 'approved', reviewer_approved: 'approved',
}
const BAD = new Set(['deny', 'auto_deny', 'cut'])

function Section({ title, help, children }) {
  return (
    <section className="pp-section">
      <h3 className="pp-h"><span>{title}</span>
        {help && <HelpTip label={`About ${title.toLowerCase()}`}>{help}</HelpTip>}</h3>
      {children}
    </section>
  )
}

function Row({ k, children }) {
  return <div className="pp-kv"><span className="pp-kv-k">{k}</span><span className="pp-kv-v">{children}</span></div>
}

// --- permission mode -----------------------------------------------------------

function ModePicker({ cid, value, onChange }) {
  const ask = useAsk()
  const [err, setErr] = useState('')
  async function pick(mode) {
    setErr('')
    const r = await applyPermissionMode({ cid, mode, value, ask, onChange })
    if (!r.ok && r.detail) setErr(r.detail)
  }
  return (
    <>
      <ul className="list-divided pp-modes" role="radiogroup" aria-label="Permission mode">
        {PERMISSION_MODES.map((m) => (
          <li key={m}>
            <button type="button" role="radio" aria-checked={m === value}
                    className={`pp-mode${m === value ? ' on' : ''}${m === 'yolo' ? ' pp-danger' : ''}`}
                    onClick={() => pick(m)}>
              <span className="pp-mode-name">{MODE_SHORT[m]}</span>
              {m === value && <span className="pp-mode-hint">{MODE_HINT[m]}</span>}
              {m === value && <UiGlyph name="check" size={16} className="pp-check" />}
            </button>
          </li>
        ))}
      </ul>
      {err && <div className="pp-err" role="alert">{err}</div>}
    </>
  )
}

// --- where it runs -------------------------------------------------------------

// A `chat-<id>` store and a chat with no scope always run in the shared box
// (vm/boxes.py for_project); a project follows its placement when boxes are on.
function RunsIn({ slug, isProject }) {
  const [boxes, setBoxes] = useState(null)
  const [place, setPlace] = useState(null)
  useEffect(() => {
    let live = true
    listBoxes().then((b) => { if (live) setBoxes(b) }).catch(() => { if (live) setBoxes(false) })
    if (isProject && slug) {
      getPlacement(slug).then((p) => { if (live) setPlace(p) }).catch(() => { if (live) setPlace(false) })
    } else setPlace(false)
    return () => { live = false }
  }, [slug, isProject])
  if (boxes === null || place === null) return <p className="pp-note">Loading…</p>
  if (boxes === false) return <p className="pp-note">The box list did not answer.</p>
  const own = !!(boxes.enabled && place && place.effective && place.effective.mode !== 'shared'
    && place.effective.box_id && place.effective.box_id !== 'shared')
  const id = own ? place.effective.box_id : 'shared'
  const box = boxes.boxes.find((b) => b.id === id) || boxes.boxes.find((b) => b.id === 'shared')
  const state = box?.state || (boxes.legacy ? (boxes.status?.running ? 'running' : 'stopped') : 'not started')
  const image = box?.image || place?.effective?.image || null
  const runtime = own ? (place.effective.runtime || box?.runtime) : (box?.runtime || 'kvm')
  const tone = state === 'running' ? 'done' : state === 'stopped' || state === 'not started' ? undefined : 'pending'
  return (
    <>
      <Row k="Where">
        {own ? 'This project’s own box' : 'The shared VM'}
        {box && <> <span className="mono pp-dim">{box.id}</span></>}
      </Row>
      <Row k="Kind">{runtime === 'docker' ? 'Container' : 'Virtual machine'}</Row>
      <Row k="Image">
        {image ? <span className="mono">{typeof image === 'string' ? image
          : `${image.variant || 'main'}${image.version ? ` v${image.version}` : ''}`}</span> : 'default'}
      </Row>
      <Row k="State"><Tag tone={tone}>{state}</Tag>
        {box?.inflight > 0 && <span className="pp-dim"> {box.inflight} running</span>}</Row>
      {place && place.effective?.mode === 'join' && (
        <Row k="Shared with">{(box?.projects || []).filter((p) => p !== slug).join(', ') || 'no one yet'}</Row>
      )}
      <p className="pp-links"><Link to="/vms">Boxes</Link></p>
    </>
  )
}

// --- network -------------------------------------------------------------------

function fold(rows) {
  const out = []
  for (const e of rows) {
    const prev = out[out.length - 1]
    if (prev && prev.host === e.host && prev.verdict === e.verdict) { prev.n += 1; continue }
    out.push({ ...e, n: 1 })
  }
  return out
}

function Network({ slug }) {
  const [policy, setPolicy] = useState(null)
  const [auto, setAuto] = useState(null)
  const [events, setEvents] = useState(null)
  useEffect(() => {
    if (!slug) { setPolicy(false); setAuto(false); setEvents([]); return undefined }
    let live = true
    getPolicy(slug).then((p) => { if (live) setPolicy(p) }).catch(() => { if (live) setPolicy(false) })
    api(`/api/egress/auto?project=${encodeURIComponent(slug)}`)
      .then((a) => { if (live) setAuto(a) }).catch(() => { if (live) setAuto(false) })
    api(`/api/egress/events?limit=60&project=${encodeURIComponent(slug)}`)
      .then((r) => { if (live) setEvents(fold(r.events || []).slice(0, 6)) })
      .catch(() => { if (live) setEvents([]) })
    return () => { live = false }
  }, [slug])
  if (!slug) return <p className="pp-note">Set by the project. Pick one under Files.</p>
  if (policy === null) return <p className="pp-note">Loading…</p>
  const prof = policy && policy.profile
  const off = prof?.network_off
  const newSites = off ? 'blocked' : (auto && auto.effective) ? 'allowed for now (auto allow is on)'
    : prof?.default === 'allow' ? 'allowed' : 'wait for you'
  return (
    <>
      <Row k="Profile">
        {prof ? <Link to="/security/profiles">{prof.name}{prof.is_default ? ' (default)' : ''}</Link> : 'unknown'}
      </Row>
      <Row k="Network">{prof ? (off ? 'Off' : 'On') : '–'}</Row>
      {!off && <Row k="New sites">{newSites}</Row>}
      {!off && policy && (
        <Row k="Lists">
          {policy.effective_allow.length} allowed, {policy.effective_deny.length} blocked
        </Row>
      )}
      <h4 className="pp-sub">Recent decisions</h4>
      {events === null ? <p className="pp-note">Loading…</p>
        : events.length === 0 ? <p className="pp-note">No traffic from this scope yet.</p>
          : (
            <ul className="list-divided pp-decs">
              {events.map((e) => (
                <li key={e.id} className="pp-dec" title={e.reason || ''}>
                  <span className="pp-dec-time mono">{tsShort(e.created_at)}</span>
                  <span className={`pp-dec-v${BAD.has(e.verdict) ? ' bad' : ''}`}>{VERDICT[e.verdict] || e.verdict}</span>
                  <span className="pp-dec-host mono ellipsis">{e.host}{e.n > 1 && <span className="pp-dim"> ×{e.n}</span>}</span>
                </li>
              ))}
            </ul>
          )}
      <p className="pp-links"><Link to="/security/network">All decisions</Link>
        {prof && <Link to="/security/profiles">Edit the profile</Link>}</p>
    </>
  )
}

export default function SecurityTab({ cid, slug, isProject, temporary, permMode, onPermMode }) {
  return (
    <div className="pp-tab">
      <Section title="Permission mode"
               help={<>
                 <p><b>Safe yolo</b>: {MODE_HINT['safe-yolo']}.</p>
                 <p><b>Auto</b>: {MODE_HINT.auto}.</p>
                 <p><b>Ask</b>: {MODE_HINT.ask}.</p>
                 <p><b>Yolo</b>: {MODE_HINT.yolo}. It needs a password login.</p>
               </>}>
        <ModePicker cid={cid} value={permMode} onChange={onPermMode} />
      </Section>
      <Section title="Runs in"
               help="Every turn runs inside a box with no key and no secrets. A project can have its own box; otherwise the shared VM.">
        {temporary ? <p className="pp-note">A temporary chat runs in the shared VM and keeps nothing.</p>
          : <RunsIn slug={slug} isProject={isProject} />}
      </Section>
      <Section title="Network"
               help="Boxes reach the network through the proxy. The profile says whether the network is on and what a site on no list does: a cut site, or a profile with the network off, is always blocked.">
        <Network slug={temporary ? null : slug} />
      </Section>
    </div>
  )
}
