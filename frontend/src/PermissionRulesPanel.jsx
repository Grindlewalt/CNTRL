import { useEffect, useState } from 'react'
import { api } from './api.js'
import { notifyError } from './notify.js'
import { Button, Card } from './components/index.js'
import { Group } from './securityParts.jsx'
import { ts } from './format.js'

// Settings: the "Yes, always allow this and similar" rules a permission ask
// stored (backend/permissions.py) — exact tool + normalised prefix, per
// project. Each is revocable; revoking means the next such call asks again.
export default function PermissionRulesPanel() {
  const [rules, setRules] = useState(null)
  const load = () => api('/api/permissions/rules').then((r) => setRules(r.rules))
    .catch(() => setRules([]))
  useEffect(() => { load() }, [])

  async function revoke(id) {
    try {
      await api(`/api/permissions/rules/${id}`, { method: 'DELETE' })
    } catch (err) { notifyError(err) }
    load()
  }

  return (
    <Card title="Permission rules" headingLevel={2}>
      <p className="sq-lede">
        Calls you chose to always allow from a permission ask (chats in auto or ask
        mode, set in the chat toolbar or with Shift+Tab in the terminal). Each rule is
        one tool and one command or path prefix, in one project.
      </p>
      {rules && rules.length === 0 && (
        <p className="sq-lede">No rules yet. Choosing "always allow" on a permission ask adds one here.</p>)}
      {rules && rules.length > 0 && (
        <Group>
          {rules.map((r) => (
            <div key={r.id} className="sk-rule">
              <span><span className="mono">{r.tool}</span> <span className="mono">{r.prefix}</span></span>
              <span className="sk-sub">
                {r.project_slug ? `In ${r.project_slug}` : 'No project'}, added {ts(r.created_at)}
              </span>
              <Button variant="ghost" onClick={() => revoke(r.id)}>Revoke</Button>
            </div>
          ))}
        </Group>
      )}
    </Card>
  )
}
