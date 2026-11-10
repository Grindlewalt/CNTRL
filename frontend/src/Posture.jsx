import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from './api.js'
import { listProfiles } from './boxes/api/profiles.js'
import { postureItems } from './securityCopy.js'

// The top of the Queue tab: what is switched on right now, in one line, each
// item a link to where it is changed (WEB-12). A newcomer opening Security
// otherwise sees a list of alerts with no idea what the defaults are (network
// policy, whether a model is deciding things, where boxes run).
//
// Read once on mount: these change rarely and every one of them is edited on a
// page that reloads its own state. A fetch that fails just drops its item.

// the rail has room for the longer names
const LABEL = { 'Unlisted sites': 'Sites no list covers', 'Auto-allow': 'Auto-allow new sites' }
const cap = (v) => (v ? String(v)[0].toUpperCase() + String(v).slice(1) : v)

export default function Posture() {
  const [state, setState] = useState(null)
  useEffect(() => {
    let live = true
    Promise.all([
      listProfiles().catch(() => null),
      api('/api/egress/auto').catch(() => null),
      api('/api/reviewer').catch(() => null),
    ]).then(([profiles, auto, reviewer]) => {
      if (live) setState({ profiles, auto, reviewer })
    })
    return () => { live = false }
  }, [])
  if (!state) return null
  // Auto review has its own switch right under this list
  const items = postureItems(state).filter((it) => it.label !== 'Auto review')
  if (!items.length) return null
  return (
    <section className="sq-rail-sec" aria-label="How it is set">
      <h3 className="sq-rail-h">How it is set</h3>
      <ul className="sq-posture">
        {items.map((it) => (
          <li key={it.label}>
            <Link to={it.to} className={it.tone || undefined} title={it.title}>
              <span>{LABEL[it.label] || it.label}</span>
              <b>{cap(it.value)}</b>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}
