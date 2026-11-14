import { useEffect, useState } from 'react'
import { api } from './api.js'
import { notifyError } from './notify.js'
import { useAsk } from './ask.jsx'
import { Button, HelpTip } from './components/index.js'
import SecretModal from './pages/SecretModal.jsx'
import { Group } from './securityParts.jsx'

// The operator's API keys (/api/secrets). The agent can reference one as
// {{secret:NAME}} and the host swaps the value in on the wire — web_read only
// on the hosts a key is bound to, the egress proxy only for projects granted
// it (that grant lives on each project's Network policy, not here). The value
// never comes back out: the API returns a name, the last four characters and
// the bound hosts, and that is all this panel ever holds.
//
// It used to be a strip in the Memory page's sidebar driven by three chained
// prompt dialogs; it is a Review tab now, because what a key may reach is a
// security decision, and it sits beside the network policy it feeds.

export default function SecretsPanel() {
  const [secrets, setSecrets] = useState(null)   // null = still loading
  const [editing, setEditing] = useState(undefined)   // undefined closed, null new, or a secret
  const ask = useAsk()

  const refresh = () => api('/api/secrets')
    .then((r) => setSecrets(r.secrets || []))
    .catch((e) => { setSecrets([]); notifyError(e) })
  useEffect(() => { refresh() }, [])

  async function del(name) {
    if (!await ask.confirm(`Delete secret ${name}?`,
                           { confirmLabel: 'Delete', danger: true })) return
    try {
      await api(`/api/secrets/${encodeURIComponent(name)}`, { method: 'DELETE' })
      await refresh()
    } catch (e) { notifyError(e) }
  }

  return (
    // No title: the Security tab strip already says "Secrets" (a tab paints no
    // heading of its own). The lede and the add button share the head row.
    <div className="sk-view">
      <div className="sk-head">
        <p>Keys the agent can use without ever reading them. <HelpTip label="About secrets">
          The agent writes <span className="mono">{'{{secret:NAME}}'}</span> and the host swaps the
          value in on the wire. A key works only on the web hosts bound to it; with none bound,
          <span className="mono"> web_read</span> refuses it.</HelpTip></p>
        <Button onClick={() => setEditing(null)}>Add secret</Button>
      </div>
      {editing !== undefined && (
        <SecretModal secret={editing} onClose={() => setEditing(undefined)} onSaved={refresh} />)}

      {secrets !== null && secrets.length === 0 && (
        <p className="sq-lede">No secrets saved. Add one to reference it from a prompt or a project.</p>
      )}
      {secrets !== null && secrets.length > 0 && (
        <Group>
          {secrets.map((s) => (
            <div key={s.name} className="sk-row">
              <div className="sk-main">
                <div className="sk-name">
                  <span className="mono">{s.name}</span>
                  <span className="sk-last4 mono" title="the last four characters">…{s.last4}</span>
                </div>
                <div className="sk-sub" title={s.hosts?.length ? s.hosts.join(', ') : undefined}>
                  {s.hosts?.length
                    ? <>Works on <span className="mono">{s.hosts.join(', ')}</span></>
                    : <span className="sq-warn-text">No web hosts bound, so it cannot be used</span>}
                </div>
              </div>
              <div className="sq-row-actions">
                <Button variant="ghost" onClick={() => setEditing(s)}>Edit</Button>
                <Button variant="ghost" danger onClick={() => del(s.name)}>Delete</Button>
              </div>
            </div>
          ))}
        </Group>
      )}
    </div>
  )
}
