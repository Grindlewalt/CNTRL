import { useState } from 'react'
import { api } from '../api.js'
import { notifyError } from '../notify.js'
import { Button, HelpTip, Input, Modal } from '../components/index.js'

// One secret in its own dialog: add a new one, or change an existing one's
// hosts (and, optionally, its value). Each web host is its own field, so no
// comma list. The server reduces a pasted URL to its hostname. The value is
// write-only: an existing secret's value is never shown, and leaving the field
// empty keeps it (the API's "value ''" = hosts-only edit).
//
//   <SecretModal secret={null | {name, last4, hosts}} onClose onSaved />

export default function SecretModal({ secret, onClose, onSaved }) {
  const isNew = !secret
  const [name, setName] = useState(secret?.name || '')
  const [value, setValue] = useState('')
  const [hosts, setHosts] = useState(secret?.hosts?.length ? [...secret.hosts] : [''])
  const [busy, setBusy] = useState(false)
  const nm = name.trim().toUpperCase()
  const ok = (!isNew || (nm && value)) && !busy

  const setHost = (i, v) => setHosts((h) => h.map((x, j) => (j === i ? v : x)))
  async function save() {
    setBusy(true)
    try {
      await api(`/api/secrets/${encodeURIComponent(isNew ? nm : secret.name)}`, {
        method: 'PUT',
        body: JSON.stringify({ value, hosts: hosts.map((h) => h.trim()).filter(Boolean) }) })
      onSaved?.()
      onClose()
    } catch (e) { notifyError(e) }
    setBusy(false)
  }

  return (
    <Modal title={isNew ? 'Add a secret' : secret.name} onClose={onClose} onSubmit={save}
           submitLabel="Save secret" submitDisabled={!ok} busy={busy} closeOnScrim={false}
           help="The agent only ever sees the name and writes {{secret:NAME}}. The host swaps the value in on the wire, and only for the sites listed here. With none listed the key cannot be used.">
      {isNew && (
        <Input label="Name" placeholder="TBA_KEY" autoFocus required value={name}
               onChange={(e) => setName(e.target.value)}
               hint="Letters, digits and underscores; stored in upper case" />
      )}
      <Input label={isNew ? 'Value' : `Replace the value (ends in …${secret.last4})`}
             type="password" autoComplete="off" required={isNew} value={value}
             onChange={(e) => setValue(e.target.value)}
             hint={isNew ? 'Stored on the host and never shown again' : 'Leave empty to keep it'} />
      <div className="field">
        <span>Sites it works on <HelpTip label="About sites">
          A host name or a pasted link both work; a link is cut down to its host name.</HelpTip></span>
        <div className="pf-hosts">
          {hosts.map((h, i) => (
            <div key={i} className="pf-host">
              <Input aria-label={`site ${i + 1}`} placeholder="api.example.com" value={h}
                     spellCheck={false} onChange={(e) => setHost(i, e.target.value)} />
              <Button variant="ghost" aria-label={`remove site ${i + 1}`}
                      onClick={() => setHosts((x) => (x.length > 1 ? x.filter((_, j) => j !== i) : ['']))}>
                Remove</Button>
            </div>
          ))}
          <Button variant="ghost" onClick={() => setHosts((x) => [...x, ''])}>Add a site</Button>
        </div>
      </div>
    </Modal>
  )
}
