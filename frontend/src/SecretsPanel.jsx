import { useEffect, useState } from 'react'
import { api } from './api.js'
import { notifyError } from './notify.js'
import { useAsk } from './ask.jsx'
import { Button, Input } from './components/index.js'
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

const splitHosts = (raw) => raw.split(',').map((h) => h.trim()).filter(Boolean)
const EMPTY = { name: '', value: '', hosts: '' }

export default function SecretsPanel() {
  const [secrets, setSecrets] = useState(null)   // null = still loading
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(EMPTY)
  const [editing, setEditing] = useState(null)   // name whose hosts are open
  const [hostsDraft, setHostsDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const ask = useAsk()

  const refresh = () => api('/api/secrets')
    .then((r) => setSecrets(r.secrets || []))
    .catch((e) => { setSecrets([]); notifyError(e) })
  useEffect(() => { refresh() }, [])

  async function put(name, body) {
    setBusy(true)
    try {
      await api(`/api/secrets/${encodeURIComponent(name)}`, {
        method: 'PUT', body: JSON.stringify(body) })
      await refresh()
      return true
    } catch (e) { notifyError(e); return false }
    finally { setBusy(false) }
  }

  async function add(e) {
    e.preventDefault()
    const name = form.name.trim()
    if (!name || !form.value) return
    if (await put(name, { value: form.value, hosts: splitHosts(form.hosts) })) {
      setForm(EMPTY); setAdding(false)
    }
  }

  function openHosts(s) {
    setEditing(s.name); setHostsDraft((s.hosts || []).join(', '))
  }
  async function saveHosts(e) {
    e.preventDefault()
    // value '' = keep the stored value; this is a hosts-only edit
    if (await put(editing, { value: '', hosts: splitHosts(hostsDraft) })) setEditing(null)
  }

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
        <p>
          Keys the agent can use as <span className="mono">{'{{secret:NAME}}'}</span> without
          ever reading them. A key works only on the web hosts bound to it; with none bound,
          <span className="mono"> web_read</span> refuses it.
        </p>
        {!adding && <Button onClick={() => setAdding(true)}>Add secret</Button>}
      </div>

      {adding && (
        <Group>
          <form className="sk-form" onSubmit={add}>
            <Input label="Name" placeholder="TBA_KEY" autoFocus required
                   value={form.name}
                   onChange={(e) => setForm({ ...form, name: e.target.value })}
                   hint="Letters, digits and underscores; stored in upper case" />
            <Input label="Value" type="password" autoComplete="off" required
                   value={form.value}
                   onChange={(e) => setForm({ ...form, value: e.target.value })}
                   hint="Stored on the host. The agent only ever sees the name." />
            <Input label="Web hosts" placeholder="newsapi.org"
                   value={form.hosts}
                   onChange={(e) => setForm({ ...form, hosts: e.target.value })}
                   hint="Comma-separated. Leave empty to keep the key unusable for now." />
            <div className="sk-form-actions">
              <Button type="submit" disabled={busy || !form.name.trim() || !form.value}>
                Save secret</Button>
              <Button variant="ghost" disabled={busy}
                      onClick={() => { setAdding(false); setForm(EMPTY) }}>Cancel</Button>
            </div>
          </form>
        </Group>
      )}

      {secrets !== null && secrets.length === 0 && !adding && (
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
                {editing === s.name ? (
                  <form className="sk-hosts-edit" onSubmit={saveHosts}>
                    <Input aria-label={`web hosts for ${s.name}`} autoFocus
                           value={hostsDraft} placeholder="Comma-separated; empty makes it unusable"
                           onChange={(e) => setHostsDraft(e.target.value)} />
                    <Button type="submit" disabled={busy}>Save</Button>
                    <Button variant="ghost" disabled={busy}
                            onClick={() => setEditing(null)}>Cancel</Button>
                  </form>
                ) : (
                  <div className="sk-sub" title={s.hosts?.length ? s.hosts.join(', ') : undefined}>
                    {s.hosts?.length
                      ? <>Works on <span className="mono">{s.hosts.join(', ')}</span></>
                      : <span className="sq-warn-text">No web hosts bound, so it cannot be used</span>}
                  </div>
                )}
              </div>
              {editing !== s.name && (
                <div className="sq-row-actions">
                  <Button variant="ghost" onClick={() => openHosts(s)}>Edit hosts</Button>
                  <Button variant="ghost" danger onClick={() => del(s.name)}>Delete</Button>
                </div>
              )}
            </div>
          ))}
        </Group>
      )}
    </div>
  )
}
