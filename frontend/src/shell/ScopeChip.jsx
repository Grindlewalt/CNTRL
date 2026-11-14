// Where this chat's work goes, as a plain button on the shell's top bar:
// "Project <name>" or "Project No project". It opens the project panel
// (panels/project) at its Files tab, where the scope is picked; it is no
// longer a dropdown of its own. `gone`: the chat is pinned to a project that
// was deleted since, which the button says rather than showing a slug the
// panel doesn't list.
export default function ScopeChip({ projects, value, gone = false, open = false, onOpen }) {
  const name = gone ? 'Deleted project'
    : value ? (projects.find((p) => p.slug === value)?.name || value) : 'No project'
  return (
    <button type="button" className={`pp-bar-btn${value && !gone ? ' pinned' : ''}${gone ? ' gone' : ''}`}
            aria-haspopup="dialog" aria-expanded={open} onClick={onOpen}>
      <span className="pp-k">Project</span>
      <span className="pp-v ellipsis">{name}</span>
    </button>
  )
}
