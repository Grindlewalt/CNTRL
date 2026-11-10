import { api } from './api.js'
import { useAsk } from './ask.jsx'
import { Button } from './components/index.js'
import { notifyError } from './notify.js'
import { useLoad } from './boxes/ui.jsx'
import { allowedLine } from './securityCopy.js'
import { Group } from './securityParts.jsx'

// Security > Persistent > "Allowed from alerts": the programs and units you
// allowed from an unexpected-process alert (POST /api/security/events/{id}/baseline),
// each with a Remove. Shown only when there is something on the list, and
// folded shut: it is a place to undo, not a page section.
//
// The exe and unit come from the guest (the alert's detail): text nodes only.

export default function AllowedProcesses() {
  const { data, reload } = useLoad(() => api('/api/security/baseline').then((r) => r.entries || []))
  const ask = useAsk()
  if (!data || data.length === 0) return null

  async function remove(e) {
    const line = allowedLine(e)
    if (!await ask.confirm(`Take ${line.what} off the list?`, {
      body: 'It alerts again the next time a box boots and it runs.',
      confirmLabel: 'Take off', danger: true })) return
    try {
      await api('/api/security/baseline/remove', {
        method: 'POST', body: JSON.stringify({ exe: e.exe, unit: e.unit || '' }) })
      reload()
    } catch (err) { notifyError(err) }
  }

  return (
    <details className="sk-fold allowed-procs">
      <summary>Allowed from alerts ({data.length})</summary>
      <p>Programs you allowed from an alert. They no longer count as unexpected in any box.</p>
      <Group>
        {data.map((e) => {
          const line = allowedLine(e)
          return (
            <div key={`${e.exe}|${e.unit}`} className="sk-rule">
              <span className="mono" title={line.what}>{line.what}</span>
              <span className="sk-sub">{line.when.replace(' · ', ', ')}</span>
              <Button variant="ghost" onClick={() => remove(e)}>Take off</Button>
            </div>
          )
        })}
      </Group>
    </details>
  )
}
