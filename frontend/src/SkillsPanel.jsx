import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from './api.js'
import { notify } from './notify.js'
import { describeUpload, fromDrop, fromFileList } from './skillDrop.js'
import HelpTip from './components/HelpTip.jsx'
import Button, { SaveButton } from './components/Button.jsx'
import Input, { Checkbox } from './components/Input.jsx'
import Toggle from './components/Toggle.jsx'
import Select from './components/Select.jsx'
import Toolbar from './components/Toolbar.jsx'
import EmptyState from './components/EmptyState.jsx'
import './pages/manage.css'

// The Skills tab of the Agents page (/agents/skills; /skills redirects here).
// Skill authoring as a fill-out form: the fields generate valid frontmatter
// server-side, so nobody hand-writes YAML. "Edit raw" stays as the escape
// hatch for anything the form doesn't cover. Rendered inside the Agents
// page's split layout, so it is an <aside> + <main> pair, not a page.
const BLANK_PARAM = { name: '', type: 'string', description: '', required: false }
const PARAM_TYPES = ['string', 'number', 'boolean', 'array']

// Bring a skill in by link or by file. A link is a GitHub repo or folder, a raw
// SKILL.md or a .zip; a file is a SKILL.md, a .zip or a folder. Both go to the
// same host-side import as the Tools page: the skill arrives switched off and
// is granted on the Tools page, never here.
function SkillImport({ onDone }) {
  const [link, setLink] = useState('')
  const [busy, setBusy] = useState(false)
  const [over, setOver] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState(null)
  const fileInput = useRef(null)
  const dirInput = useRef(null)

  async function finish(r) {
    const flags = r.flags?.length ? `, ${r.flags.length} flagged` : ''
    notify(`Imported ${r.name}, not granted${flags}`, r.flags?.length ? { sev: 'warn' } : {})
    setDone(r.name)
    setLink('')
    onDone()
  }
  async function byLink() {
    if (!link.trim() || busy) return
    setBusy(true); setErr(''); setDone(null)
    try {
      finish(await api('/api/skills/import', {
        method: 'POST', body: JSON.stringify({ source: link.trim() }) }))
    } catch (e) { setErr(e.detail || e.message || 'Import failed') }
    setBusy(false)
  }
  async function byFiles(items) {
    if (!items.length) { setErr('Nothing to import there.'); return }
    setBusy(true); setErr(''); setDone(null)
    try {
      const form = new FormData()
      for (const { path, file } of items) form.append('files', file, path)
      const res = await fetch('/api/skills/import/upload', { method: 'POST', body: form })
      if (!res.ok) {
        let detail = res.statusText
        try { detail = (await res.json()).detail || detail } catch { /* not json */ }
        throw new Error(detail)
      }
      finish(await res.json())
    } catch (e) { setErr(e.message || 'Import failed') }
    setBusy(false)
  }
  return (
    <div className="skill-add">
      <div className="row">
        <Input aria-label="Skill link" value={link} disabled={busy}
               placeholder="Paste a GitHub, SKILL.md or .zip link"
               onChange={(e) => setLink(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); byLink() } }} />
        <Button onClick={byLink} disabled={busy || !link.trim()}>{busy ? 'Importing…' : 'Import'}</Button>
      </div>
      <div className={`tool-drop${over ? ' over' : ''}`}
           onDragOver={(e) => { e.preventDefault(); setOver(true) }}
           onDragLeave={() => setOver(false)}
           onDrop={async (e) => {
             e.preventDefault(); setOver(false)
             try { byFiles(await fromDrop(e.dataTransfer)) } catch (x) { setErr(x.message) }
           }}>
        <span className="grow dim small">Or drop a SKILL.md, .zip or folder</span>
        <Button variant="ghost" onClick={() => fileInput.current?.click()}>Choose a file</Button>
        <Button variant="ghost" onClick={() => dirInput.current?.click()}>Choose a folder</Button>
        <input ref={fileInput} type="file" accept=".zip,.md,application/zip,text/markdown" hidden
               onChange={(e) => { byFiles(fromFileList(e.target.files)); e.target.value = '' }} />
        <input ref={dirInput} type="file" webkitdirectory="" hidden
               onChange={(e) => { byFiles(fromFileList(e.target.files)); e.target.value = '' }} />
      </div>
      {done && <span className="small">Imported <code>{done}</code>, switched off. Grant
        it on the <Link to="/tools">Tools page</Link>.</span>}
      {err && <span className="error" role="alert">{err}</span>}
    </div>
  )
}

export default function SkillsPanel() {
  const [skills, setSkills] = useState([])
  const [selected, setSelected] = useState(null)
  const [fields, setFields] = useState(null)
  const [content, setContent] = useState('')
  const [raw, setRaw] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [error, setError] = useState(null)
  const [adding, setAdding] = useState(false)

  // imported (OpenClaw) skills are pinned — read and granted on the Tools page,
  // never edited here (the server refuses the save anyway)
  const refresh = () => api('/api/skills')
    .then((r) => setSkills(r.skills.filter((s) => !s.imported)))
  useEffect(() => { refresh() }, [])

  useEffect(() => {
    if (!selected) return
    api(`/api/skills/${selected}`).then((r) => {
      setContent(r.content)
      setFields(r.fields)
      setDirty(false)
    })
  }, [selected])

  const set = (p) => { setFields((f) => ({ ...f, ...p })); setDirty(true) }
  const setParam = (i, p) => set({
    params: fields.params.map((x, j) => (j === i ? { ...x, ...p } : x)) })

  async function create(e) {
    e.preventDefault()
    setError(null)
    // said on the page: the browser's own "required" bubble does not show in
    // every window, and Create then looked dead (WEBA-15)
    if (!name.trim()) { setError('give the skill a name'); return }
    try {
      const r = await api('/api/skills', {
        method: 'POST',
        body: JSON.stringify({ name, description: desc || undefined }),
      })
      setName(''); setDesc('')
      await refresh()
      setSelected(r.slug)
    } catch (err) { setError(err.detail) }
  }

  async function save() {
    if (raw) {
      await api(`/api/skills/${selected}`, {
        method: 'PUT', body: JSON.stringify({ content }) })
    } else {
      await api(`/api/skills/${selected}/fields`, {
        method: 'PUT', body: JSON.stringify(fields) })
    }
    setDirty(false)
    // reload both representations so switching views stays consistent
    const r = await api(`/api/skills/${selected}`)
    setContent(r.content); setFields(r.fields)
    refresh()
  }

  return (
    <>
      <aside className="skills-aside">
        <form className="stack" onSubmit={create} noValidate>
          <Input label="New skill" placeholder="Skill name" value={name}
                 aria-label="New skill name" onChange={(e) => setName(e.target.value)} />
          <Input placeholder="What it does, in a line" value={desc}
                 aria-label="What the skill does" onChange={(e) => setDesc(e.target.value)} />
          <Button type="submit">Create skill</Button>
          {error && <span className="error">{error}</span>}
        </form>
        <Button variant="ghost" aria-expanded={adding} onClick={() => setAdding((v) => !v)}>
          {adding ? 'Close import' : 'Import a skill'}
        </Button>
        {adding && <SkillImport onDone={refresh} />}
        <ul className="agent-list list-divided">
          {skills.map((s) => (
            <li key={s.slug}>
              <button type="button" aria-current={selected === s.slug || undefined}
                      className={selected === s.slug ? 'agent-row active' : 'agent-row'}
                      onClick={() => setSelected(s.slug)}>
                <span className="agent-row-name">{s.name}</span>
                <span className="agent-row-sub">
                  <span className={s.enabled ? 'small skill-state ok' : 'small skill-state'}>
                    {s.enabled ? 'Granted' : 'Not granted'}</span>
                </span>
              </button>
            </li>
          ))}
          {skills.length === 0 && <EmptyState as="li">No skills yet. Name one above to start.</EmptyState>}
        </ul>
        <p className="dim small">Skills your agents can use.
          <HelpTip label="About skills">A skill teaches CNTRL a procedure. It sees the
            name and the "use when" line every turn and reads the full instructions
            only when it uses the skill. Agents get every granted skill unless their
            definition takes one away. Imported skills arrive switched off and are
            granted on the Tools page.</HelpTip></p>
      </aside>
      <main className={selected ? 'editor-pane skill-pane' : 'editor-pane split-idle'}>
        {!selected ? (
          <EmptyState pad>Pick a skill on the left to edit it, or create one.</EmptyState>
        ) : (
          <>
            <Toolbar variant="pane"
                     title={skills.find((x) => x.slug === selected)?.name || selected}>
              <Button variant="ghost" onClick={() => setRaw((v) => !v)}>
                {raw ? 'Form editor' : 'Edit raw'}</Button>
              <SaveButton dirty={dirty} onSave={save} />
            </Toolbar>
            {raw || !fields ? (
              <textarea className="md-editor grow" spellCheck={false} value={content}
                        aria-label="SKILL.md"
                        onChange={(e) => { setContent(e.target.value); setDirty(true) }} />
            ) : (
              <div className="skill-form">
                <Input label="what it does (shown to CNTRL every turn)"
                       value={fields.description}
                       onChange={(e) => set({ description: e.target.value })} />
                <Input label="use when… (how CNTRL decides to pick it)"
                       value={fields.when_to_use}
                       onChange={(e) => set({ when_to_use: e.target.value })} />
                <Toggle checked={!!fields.enabled} label="granted to CNTRL and agents"
                        onText="granted to CNTRL and agents"
                        offText="not granted — CNTRL and agents can't use it"
                        onChange={(v) => set({ enabled: v })} />
                <Input textarea rows={12} className="md-editor" spellCheck={false}
                       label="instructions (loaded when the skill is invoked)"
                       value={fields.body}
                       onChange={(e) => set({ body: e.target.value })} />
                <div className="field-hint">arguments (optional)</div>
                {fields.params.map((p, i) => (
                  <div className="row skill-param" key={i}>
                    <input placeholder="name" value={p.name} aria-label="argument name"
                           className="skill-param-name"
                           onChange={(e) => setParam(i, { name: e.target.value })} />
                    <Select value={p.type} options={PARAM_TYPES} aria-label="argument type"
                            onChange={(e) => setParam(i, { type: e.target.value })} />
                    <input className="grow" placeholder="description" value={p.description}
                           aria-label="argument description"
                           onChange={(e) => setParam(i, { description: e.target.value })} />
                    <Checkbox checked={p.required} label="req" title="required"
                              onChange={(e) => setParam(i, { required: e.target.checked })} />
                    <button className="win-btn" type="button" aria-label="remove argument"
                            onClick={() => set({ params: fields.params.filter((_, j) => j !== i) })}>×</button>
                  </div>
                ))}
                <Button variant="ghost"
                        onClick={() => set({ params: [...fields.params, { ...BLANK_PARAM }] })}>
                  + argument</Button>
              </div>
            )}
          </>
        )}
      </main>
    </>
  )
}
