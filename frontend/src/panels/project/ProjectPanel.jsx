import { useEffect, useState } from 'react'
import { SidePanel } from '../../components/index.js'
import RunsTab from '../../shell/RunsTab.jsx'
import SecurityTab from './SecurityTab.jsx'
import FilesTab from './FilesTab.jsx'
import FileViewer from './FileViewer.jsx'

// The chat's project panel: what the toolbar's buttons open. Three tabs.
//   Security  permission mode, where the chat runs, network
//   Files     the scope's files, this chat's artifacts, published pages;
//             a row opens the viewer in place (the panel widens for it)
//   Runs      the multi-agent runs around this chat (shell/RunsTab)
//
// Props: open, tab, onTab, onClose; cid, temporary; slug (the store: the
// project, else `chat-<id>`), isProject; projects, loaded (the globally loaded
// project) and scope { mode, value, onPick(mode, slug) } for the Files tab's
// "Works in" control; permMode / onPermMode; runs (offer the tab), chatJobs.

export default function ProjectPanel({
  open, tab, onTab, onClose, cid, temporary, slug, isProject, projects, loaded, scope,
  permMode, onPermMode, runs, chatJobs = [],
}) {
  const [file, setFile] = useState(null)       // { slug, path, mode } open in the viewer
  const [version, setVersion] = useState(0)    // bumps when a save may have changed a list
  useEffect(() => { setFile(null) }, [slug])
  const running = chatJobs.filter((j) => j.running).length
  const tabs = [
    { id: 'security', label: 'Security' },
    { id: 'files', label: 'Files' },
    runs && { id: 'runs', label: 'Runs', count: running },
  ].filter(Boolean)
  const title = isProject ? (projects.find((p) => p.slug === slug)?.name || slug)
    : temporary ? 'Temporary chat' : cid ? 'This chat' : 'New chat'
  const viewing = tab === 'files' && !!file
  return (
    <SidePanel open={open} onClose={onClose} title={title} tabs={tabs} tab={tab} onTab={onTab}
               width={viewing ? 760 : 440} className={`pp-panel${viewing ? ' viewing' : ''}`}>
      {(t) => {
        if (t === 'security') {
          return <SecurityTab cid={cid} slug={slug} isProject={isProject} temporary={temporary}
                              permMode={permMode} onPermMode={onPermMode} />
        }
        if (t === 'runs') return <RunsTab project={isProject ? slug : null} chatJobs={chatJobs} />
        if (file) {
          return <FileViewer slug={file.slug} path={file.path} mode={file.mode}
                             onBack={() => setFile(null)} onChanged={() => setVersion((v) => v + 1)} />
        }
        return <FilesTab slug={slug} isProject={isProject} cid={cid} temporary={temporary}
                         projects={projects} loaded={loaded} scope={scope} version={version}
                         onOpen={(s, path, mode) => setFile({ slug: s, path, mode })} />
      }}
    </SidePanel>
  )
}
