import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api.js'
import { EmptyState, HelpTip, Select, useRowMenu } from '../../components/index.js'
import { ago, human } from '../../format.js'
import { rawUrl } from '../shared.js'
import { isRunnable, isText } from './FileViewer.jsx'

// Everything the chat's scope has, in one list: the store's files (the
// project's, or a project-less chat's own `chat-<id>` store), the chat's own
// artifacts when it is pinned to a project, and the pages it has published.
// A row opens the viewer; its menu (⋯ or right-click) has Open, Edit for
// text, Run for .py, Download and the raw link. "Download all" is the
// server's zip of the listed files (GET /zip, same login as the list).
//
// The scope row at the top is where the chat's project is picked: follow the
// loaded project, no project, or pinned to one. It is the Select that
// replaced the toolbar's project dropdown.
//
// Two optional widenings: `agent` ({slug, name}, an agent chat) adds that
// agent's outputs (GET /api/agents/<slug>/outputs, the Agents page's Outputs
// data) with their files, and the pages its threads published; `chatIds` (a
// project's home: the project's chats) adds those chats' own artifact stores
// (GET /api/artifacts, filtered to them) and the pages they published.

// `version` bumps when a save or a run may have changed the store
function useStoreFiles(slug, version = 0) {
  const [files, setFiles] = useState(null)   // null loading, [] none, false: no store yet
  const load = useCallback(() => {
    if (!slug) { setFiles([]); return }
    api(`/api/projects/${encodeURIComponent(slug)}/files`)
      .then((r) => setFiles(r.files || []))
      .catch((e) => setFiles(e.status === 404 ? false : []))
  }, [slug])
  useEffect(() => { setFiles(null); load() }, [load])
  useEffect(() => { if (version) load() }, [version, load])
  return files
}

// The agent's outputs, newest first: null while loading or with no agent
function useAgentOutputs(slug, version = 0) {
  const [outs, setOuts] = useState(null)
  useEffect(() => {
    if (!slug) { setOuts(null); return undefined }
    let live = true
    api(`/api/agents/${encodeURIComponent(slug)}/outputs?limit=50`)
      .then((r) => { if (live) setOuts(r.outputs || []) })
      .catch(() => { if (live) setOuts([]) })
    return () => { live = false }
  }, [slug, version])
  return outs
}

// The artifact stores (`chat-<id>`) of these chats, the ones with files
function useArtifacts(ids, version = 0) {
  const key = (ids || []).join(',')
  const [stores, setStores] = useState([])
  useEffect(() => {
    if (!key) { setStores([]); return undefined }
    const want = new Set(key.split(',').map(Number))
    let live = true
    api('/api/artifacts')
      .then((r) => { if (live) setStores((r.artifacts || []).filter((a) => want.has(a.chat_id))) })
      .catch(() => { if (live) setStores([]) })
    return () => { live = false }
  }, [key, version])
  return stores
}

// Published pages from this scope: the project's, the open chat's, and any
// from a thread in `ids` (an agent's outputs, a project's chats)
function usePages({ slug, isProject, cid, ids = [] }) {
  const key = ids.join(',')
  const [pages, setPages] = useState([])
  useEffect(() => {
    if (!slug && !cid && !key) { setPages([]); return undefined }
    const want = new Set(key ? key.split(',').map(Number) : [])
    let live = true
    api('/api/pages').then((r) => {
      if (live) {
        setPages((r.items || []).filter((p) => (isProject && p.project === slug)
          || (cid && p.conversation_id === cid) || want.has(p.conversation_id)))
      }
    }).catch(() => { if (live) setPages([]) })
    return () => { live = false }
  }, [slug, isProject, cid, key])
  return pages
}

// What the project home's Files row counts: exactly what this tab lists for
// the project (its files, its chats' artifacts, its published pages)
export function useFilesSummary({ slug, chatIds = [] }) {
  const files = useStoreFiles(slug)
  const stores = useArtifacts(chatIds)
  const pages = usePages({ slug, isProject: true, cid: null, ids: chatIds })
  return {
    files: Array.isArray(files) ? files.length : files === null ? null : 0,
    artifacts: stores.reduce((n, a) => n + (a.files?.length || 0), 0),
    pages: pages.length,
  }
}

function FileRow({ slug, f, onOpen }) {
  const name = f.path.split('/').pop()
  const dir = f.path.slice(0, f.path.length - name.length)
  const raw = rawUrl(slug, f.path)
  const download = () => {
    const a = document.createElement('a')
    a.href = raw; a.download = name; a.rel = 'noreferrer'
    document.body.appendChild(a); a.click(); a.remove()
  }
  const { rowProps, menu, open } = useRowMenu([
    { label: 'Open', onSelect: () => onOpen(f.path, 'view') },
    isText(f.path) && { label: 'Edit', onSelect: () => onOpen(f.path, 'edit') },
    isRunnable(f.path) && { label: 'Run', onSelect: () => onOpen(f.path, 'run') },
    '-',
    { label: 'Download', onSelect: download },
    { label: 'Open raw in a new tab', onSelect: () => window.open(raw, '_blank', 'noreferrer') },
  ], { label: `${name} actions`, buttonClassName: 'icon-btn pp-more' })
  return (
    <li {...rowProps} className={`pp-file${open ? ' menu-open' : ''}`}>
      <button type="button" className="pp-file-open" onClick={() => onOpen(f.path, 'view')}
              title={f.path}>
        <span className="pp-name mono ellipsis">{dir && <span className="pp-dir">{dir}</span>}{name}</span>
        {f.size != null && <span className="pp-size mono">{human(f.size)}</span>}
      </button>
      {menu}
    </li>
  )
}

function FileList({ slug, files, onOpen, empty }) {
  if (files === null) return <p className="pp-note">Loading…</p>
  if (files === false || files.length === 0) return <p className="pp-note">{empty}</p>
  return (
    <ul className="list-divided pp-files">
      {files.map((f) => <FileRow key={f.path} slug={slug} f={f} onOpen={onOpen} />)}
    </ul>
  )
}

function Section({ title, count, help, action, children }) {
  return (
    <section className="pp-section">
      <h3 className="pp-h">
        <span>{title}</span>
        {count > 0 && <span className="pp-count mono">{count}</span>}
        {help && <HelpTip label={`About ${title.toLowerCase()}`}>{help}</HelpTip>}
        <span className="grow" />
        {action}
      </h3>
      {children}
    </section>
  )
}

function PageRow({ p }) {
  const { rowProps, menu, open } = useRowMenu([
    p.url && { label: 'Open page', onSelect: () => window.open(p.url, '_blank', 'noreferrer') },
    p.url && { label: 'Copy link', onSelect: () => navigator.clipboard?.writeText(p.url) },
  ], { label: `${p.name} actions`, buttonClassName: 'icon-btn pp-more' })
  return (
    <li {...rowProps} className={`pp-file${open ? ' menu-open' : ''}`}>
      {p.url
        ? <a className="pp-file-open" href={p.url} target="_blank" rel="noreferrer" title={p.url}>
            <span className="pp-name ellipsis">{p.name}</span>
            <span className="pp-size mono">{p.files?.length || 0} files</span>
          </a>
        : <span className="pp-file-open">
            <span className="pp-name ellipsis">{p.name}</span>
            <span className="pp-size">{p.status}</span>
          </span>}
      {menu}
    </li>
  )
}

// "[Name] task…" titles carry the agent's prefix; the section already says whose
const outputTitle = (o) => (o.title || '').replace(/^\[[^\]]*\]\s*/, '') || `#${o.id}`
const OUTPUT_KIND = { chat: 'chat', agent: 'run', head: 'job' }

// One output of the agent: its title and age; its files (a job's rollup
// files) open in the viewer like any other. The menu opens the thread: a chat
// in this chat view, anything else on the Outputs tab.
function OutputItem({ o, agent, onOpen, onOpenChat }) {
  const navigate = useNavigate()
  const inChat = o.kind === 'chat' && onOpenChat
  const { rowProps, menu, open } = useRowMenu([
    inChat && { label: 'Open in chat', onSelect: () => onOpenChat(o.id) },
    { label: 'Open on the Outputs tab',
      onSelect: () => navigate(`/agents/outputs/${encodeURIComponent(agent.slug)}`) },
  ], { label: `${outputTitle(o)} actions`, buttonClassName: 'icon-btn pp-more' })
  const when = o.last_at || o.started_at
  return (
    <li className="pp-output">
      <div {...rowProps} className={`pp-file${open ? ' menu-open' : ''}`}>
        <button type="button" className="pp-file-open" title={o.title || ''}
                onClick={() => (inChat ? onOpenChat(o.id)
                  : navigate(`/agents/outputs/${encodeURIComponent(agent.slug)}`))}>
          <span className="pp-name ellipsis">{outputTitle(o)}</span>
          <span className="pp-size">{o.running ? 'running' : `${OUTPUT_KIND[o.kind] || o.kind}, ${ago(when)}`}</span>
        </button>
        {menu}
      </div>
      {o.project && o.runs_files?.length > 0 && (
        <ul className="list-divided pp-files pp-output-files">
          {o.runs_files.map((path) => (
            <FileRow key={path} slug={o.project} f={{ path, size: null }}
                     onOpen={(p, mode) => onOpen(o.project, p, mode)} />
          ))}
        </ul>
      )}
    </li>
  )
}

export default function FilesTab({
  slug, isProject, cid, temporary, projects = [], loaded, scope, onOpen, version = 0,
  agent = null, chatIds = [], onOpenChat,
}) {
  const files = useStoreFiles(slug, version)
  const artSlug = isProject && cid ? `chat-${cid}` : null
  const artifacts = useStoreFiles(artSlug, version)
  const outputs = useAgentOutputs(agent?.slug, version)
  // the threads whose artifacts and pages belong here besides the open chat
  const threadIds = [...new Set([...chatIds, ...(outputs || []).map((o) => o.id)])]
    .filter((id) => id !== cid)
  const stores = useArtifacts(threadIds, version)
  const pages = usePages({ slug, isProject, cid, ids: threadIds })

  // the scope Select: follow / none / pin:<slug>
  const scopeValue = scope ? (scope.mode === 'pin' ? `pin:${scope.value}` : scope.mode) : ''
  const scopeOptions = [
    !scope?.noFollow && { value: 'follow', label: 'Follow the loaded project',
      sub: loaded ? (projects.find((p) => p.slug === loaded)?.name || loaded) : 'nothing is loaded' },
    { value: 'none', label: 'No project', sub: 'files stay with this chat' },
    ...projects.map((p) => ({ value: `pin:${p.slug}`, label: p.name, group: 'Pin to a project' })),
  ].filter(Boolean)
  const pickScope = (v) => {
    if (v.startsWith('pin:')) scope.onPick('pin', v.slice(4))
    else scope.onPick(v, '')
  }

  const projectName = isProject ? (projects.find((p) => p.slug === slug)?.name || slug) : null
  return (
    <div className="pp-tab">
      {scope && (
        <div className="pp-scope">
          <Select label="Works in" value={scopeValue} onChange={(e) => pickScope(e.target.value)}
                  options={scopeOptions} recentKey={false} className="pp-scope-select" />
          <HelpTip label="About where a chat works">
            Files this chat writes go to the project it works in. Following the loaded
            project means the one setting for the whole server; pinning keeps this chat on
            one project whatever is loaded; no project keeps its files with the chat.
          </HelpTip>
        </div>
      )}
      {temporary ? (
        <EmptyState pad>A temporary chat writes nothing to disk, so there is nothing here.</EmptyState>
      ) : (
        <>
          <Section title={projectName ? 'Files' : 'This chat’s files'} count={files?.length || 0}
                   help={projectName
                     ? `The files in the ${projectName} project, as the agent and you leave them. Open renders a file; Edit saves through the same checks as the agent’s writes.`
                     : 'Files the agent writes in a chat with no project stay in the chat’s own store.'}
                   action={slug && files && files.length > 0 && (
                     <a className="ghost-link pp-zip" href={`/api/projects/${encodeURIComponent(slug)}/zip`}
                        download={`${slug}.zip`}>Download all</a>
                   )}>
            <FileList slug={slug} files={files} onOpen={(path, mode) => onOpen(slug, path, mode)}
                      empty={slug ? 'No files yet. Anything the chat writes shows up here.'
                        : 'This chat has no files yet. Anything it writes shows up here once it has started.'} />
          </Section>
          {artSlug && artifacts && artifacts.length > 0 && (
            <Section title="This chat’s artifacts" count={artifacts.length}
                     help="Files this chat wrote before it was pinned to the project, kept in its own store."
                     action={(
                       <a className="ghost-link pp-zip" href={`/api/projects/${artSlug}/zip`}
                          download={`${artSlug}.zip`}>Download all</a>
                     )}>
              <FileList slug={artSlug} files={artifacts}
                        onOpen={(path, mode) => onOpen(artSlug, path, mode)} empty="" />
            </Section>
          )}
          {stores.length > 0 && (
            <Section title="Artifacts" count={stores.reduce((n, a) => n + a.files.length, 0)}
                     help={agent
                       ? `Files ${agent.name}’s chats wrote with no project, each kept in the chat’s own store.`
                       : 'Files this project’s chats wrote before they were pinned to it, each kept in the chat’s own store.'}>
              {stores.map((a) => (
                <div key={a.slug} className="pp-store">
                  <h4 className="pp-sub pp-store-h">
                    <span className="ellipsis">{a.title}</span>
                    <a className="ghost-link pp-zip" href={`/api/projects/${encodeURIComponent(a.slug)}/zip`}
                       download={`${a.slug}.zip`}>Download all</a>
                  </h4>
                  <FileList slug={a.slug} files={a.files}
                            onOpen={(path, mode) => onOpen(a.slug, path, mode)} empty="" />
                </div>
              ))}
            </Section>
          )}
          {agent && (
            <Section title={`${agent.name}’s outputs`} count={outputs?.length || 0}
                     help={`Everything that ran as ${agent.name}, newest first: its chats, its runs and the jobs they started. The same list as the Outputs tab.`}>
              {outputs === null ? <p className="pp-note">Loading…</p>
                : outputs.length === 0 ? <p className="pp-note">Nothing has run as {agent.name} yet.</p>
                  : (
                    <ul className="list-divided pp-files pp-outputs">
                      {outputs.map((o) => <OutputItem key={o.id} o={o} agent={agent}
                                                      onOpen={onOpen} onOpenChat={onOpenChat} />)}
                    </ul>
                  )}
            </Section>
          )}
          <Section title="Published pages" count={pages.length}
                   help="Pages the agent asked to publish and you approved, served read-only to anyone with the link.">
            {pages.length === 0
              ? <p className="pp-note">Nothing published from here.</p>
              : <ul className="list-divided pp-files">{pages.map((p) => <PageRow key={p.id} p={p} />)}</ul>}
          </Section>
        </>
      )}
    </div>
  )
}
