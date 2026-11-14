import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api.js'
import Md from '../../Md.jsx'
import { Button, EmptyState, SaveButton } from '../../components/index.js'
import { UiGlyph } from '../../shell/glyphs.jsx'
import { human } from '../../format.js'
import { IMG_EXT, TEXT_EXT, rawUrl } from '../shared.js'
import { withCsp } from '../RendererPanel.jsx'
import RunPanel from '../RunPanel.jsx'
import { tint } from './tint.js'

// One file of a store (a project, or a chat's own `chat-<id>` store), opened
// from the Files tab. View renders what the file is: Markdown, HTML in a
// sandboxed frame (allow-scripts only, never allow-same-origin, plus the
// renderer's CSP that denies the network), a PDF in the browser's viewer, an
// image, or text with a syntax tint. Edit is a textarea saved through PUT
// /file, which lands through writes.apply_write (the agent's chokepoint): a
// protected path or a real secret value is refused and the refusal is shown.
// Run mounts the Run panel with the file picked.

export const kindOf = (path) => {
  if (/\.(md|markdown)$/i.test(path)) return 'md'
  if (/\.html?$/i.test(path)) return 'html'
  if (/\.pdf$/i.test(path)) return 'pdf'
  if (IMG_EXT.test(path)) return 'image'
  if (TEXT_EXT.test(path) || /\.(rs|go|java|kt|swift|c|cc|cpp|h|rb|php|sql|ini|cfg|conf|log|env|txt|xml|svg|gitignore|lock)$/i.test(path)) return 'text'
  return 'other'
}
export const isText = (path) => ['md', 'html', 'text'].includes(kindOf(path))
export const isRunnable = (path) => /\.py$/i.test(path)

function Tinted({ text, path }) {
  const toks = useMemo(() => tint(text, path), [text, path])
  return (
    <pre className="pp-code">
      {toks.map((t, i) => (t.t ? <span key={i} className={`pp-tok-${t.t}`}>{t.v}</span> : t.v))}
    </pre>
  )
}

export default function FileViewer({ slug, path, mode: initialMode = 'view', onBack, onChanged }) {
  const [mode, setMode] = useState(initialMode)
  const [file, setFile] = useState(null)      // { binary, content, size } | null while loading
  const [err, setErr] = useState('')
  const [draft, setDraft] = useState('')
  const [dirty, setDirty] = useState(false)
  const [run, setRun] = useState({})
  const kind = kindOf(path)
  const text = isText(path)
  const url = rawUrl(slug, path)

  useEffect(() => {
    setFile(null); setErr(''); setDirty(false)
    let live = true
    api(`/api/projects/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`)
      .then((r) => { if (live) { setFile(r); setDraft(r.binary ? '' : (r.content || '')) } })
      .catch((e) => { if (live) setErr(e.detail || String(e)) })
    return () => { live = false }
  }, [slug, path])

  useEffect(() => { setMode(initialMode) }, [initialMode, path])

  async function save() {
    setErr('')
    try {
      await api(`/api/projects/${encodeURIComponent(slug)}/file`, {
        method: 'PUT', body: JSON.stringify({ path, content: draft }) })
      setFile((f) => ({ ...f, content: draft }))
      setDirty(false)
      onChanged?.()
    } catch (e) {
      setErr(e.detail || String(e))   // a refused save says why (a secret value, a protected path)
    }
  }

  const name = path.split('/').pop()
  const dir = path.slice(0, path.length - name.length)
  let body
  if (err && !file) body = <EmptyState pad>{err}</EmptyState>
  else if (!file) body = <EmptyState pad>Loading…</EmptyState>
  else if (mode === 'run') {
    body = <RunPanel slug={slug} state={{ runFile: path, code: '', ...run }}
                     setState={(p) => setRun((s) => ({ ...s, ...p }))} />
  } else if (mode === 'edit' && text && !file.binary) {
    body = (
      <textarea className="md-editor pp-editor" spellCheck={false} value={draft}
                aria-label={`Edit ${path}`}
                onChange={(e) => { setDraft(e.target.value); setDirty(true) }} />
    )
  } else if (kind === 'html') {
    body = <iframe className="pp-frame" sandbox="allow-scripts" title={path}
                   srcDoc={withCsp(file.content || '')} />
  } else if (kind === 'pdf') {
    body = <embed className="pp-frame" src={url} type="application/pdf" />
  } else if (kind === 'image') {
    body = <div className="pp-media"><img src={url} alt={path} /></div>
  } else if (file.binary) {
    body = <EmptyState pad>A binary file of {human(file.size)}. Download it to open it elsewhere.</EmptyState>
  } else if (kind === 'md') {
    body = <div className="pp-md"><Md text={file.content || ''} /></div>
  } else {
    body = <Tinted text={file.content || ''} path={path} />
  }

  return (
    <div className="pp-viewer">
      <div className="pp-viewer-head">
        <button type="button" className="icon-btn" aria-label="Back to the list" title="Back"
                onClick={onBack}><UiGlyph name="left" size={18} /></button>
        <span className="pp-viewer-path mono ellipsis" title={path}>
          {dir && <span className="pp-dir">{dir}</span>}{name}
        </span>
        <div className="pp-viewer-actions">
          {text && !file?.binary && (
            mode === 'edit'
              ? <>
                  <SaveButton dirty={dirty} onSave={save} />
                  <Button variant="ghost" onClick={() => { setMode('view'); setDraft(file.content || ''); setDirty(false) }}>Done</Button>
                </>
              : <Button variant="ghost" onClick={() => setMode('edit')}>Edit</Button>
          )}
          {isRunnable(path) && mode !== 'edit' && (
            <Button variant="ghost" aria-pressed={mode === 'run'}
                    onClick={() => setMode(mode === 'run' ? 'view' : 'run')}>
              {mode === 'run' ? 'View' : 'Run'}</Button>
          )}
          <a className="ghost-link" href={url} download={name}>Download</a>
        </div>
      </div>
      {err && file && <div className="pp-err" role="alert">{err}</div>}
      <div className={`pp-viewer-body${mode === 'edit' ? ' editing' : ''}`}>{body}</div>
    </div>
  )
}
