import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api.js'
import { EmptyState, HelpTip, Select, useRowMenu } from '../../components/index.js'
import { human } from '../../format.js'
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
        <span className="pp-size mono">{human(f.size)}</span>
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

export default function FilesTab({
  slug, isProject, cid, temporary, projects = [], loaded, scope, onOpen, version = 0,
}) {
  const files = useStoreFiles(slug, version)
  const artSlug = isProject && cid ? `chat-${cid}` : null
  const artifacts = useStoreFiles(artSlug, version)
  const [pages, setPages] = useState([])
  useEffect(() => {
    if (!slug && !cid) { setPages([]); return }
    api('/api/pages').then((r) => setPages((r.items || []).filter((p) =>
      (isProject && p.project === slug) || (cid && p.conversation_id === cid)))).catch(() => setPages([]))
  }, [slug, isProject, cid])

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
