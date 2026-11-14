import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useDismiss } from '../useDismiss.js'
import Tabs from './Tabs.jsx'
import { UiGlyph } from '../shell/glyphs.jsx'

// A panel that slides in from the right: the chat's project panel (Security,
// Files, Runs), a detail view beside a list. Full screen on a phone.
//
//   <SidePanel open={open} onClose={() => setOpen(false)} title="Project"
//              tabs={[{ id: 'security', label: 'Security' }, { id: 'files', label: 'Files' }]}
//              tab={tab} onTab={setTab}>
//     {tab === 'security' ? <SecurityTab /> : <FilesTab />}
//   </SidePanel>
//
// Props:
//   open, onClose        shown while open; Escape and the close button call onClose
//   title                the heading, and the dialog's name
//   tabs                 optional [{ id, label, count? }]: a tab strip under the title
//   tab / onTab          the current tab, controlled; or defaultTab, uncontrolled.
//                        children may be a function of the current tab id.
//   mode                 'over' (default): fixed over the page's right edge.
//                        'beside': in the page's flow, so a flex row gives it
//                        its own column and the page narrows instead of being
//                        covered. Both are full screen on a phone.
//   width                desktop width in px (default 420)
//   footer               optional bar at the bottom (actions)
//   className            on the panel
//
// Escape closes one layer at a time (useDismiss's stack), so a Select or a
// menu open inside the panel closes first. A click outside does not close it:
// it sits beside work the operator is still doing. While open, Tab and
// Shift+Tab cycle inside the panel; focus moves in on open and goes back to
// where it was on close. Popovers portalled out of the panel (Select, menus)
// still take focus: the trap only wraps Tab at the panel's two ends.
const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled):not([type="hidden"]), '
  + 'select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
// every pointerdown counts as "inside", so useDismiss gives Escape only
const NEVER_OUTSIDE = { current: { contains: () => true } }

export default function SidePanel({
  open, onClose, title, tabs, tab, defaultTab, onTab, mode = 'over', width = 420,
  footer, className = '', children,
}) {
  const titleId = useId()
  const [innerTab, setInnerTab] = useState(defaultTab ?? tabs?.[0]?.id)
  const current = tab !== undefined ? tab : innerTab
  const setTab = (id) => { if (tab === undefined) setInnerTab(id); onTab?.(id) }
  const panelRef = useDismiss(open, onClose || (() => {}), NEVER_OUTSIDE)
  const back = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    back.current = document.activeElement
    const el = panelRef.current
    // the panel itself first (its heading is read), unless something inside
    // already took focus (an autoFocus field)
    if (el && !el.contains(document.activeElement)) el.focus({ preventScroll: true })
    return () => {
      const b = back.current
      if (b && document.contains(b)) b.focus?.({ preventScroll: true })
    }
  }, [open, panelRef])

  if (!open) return null
  const onKeyDown = (e) => {
    if (e.key !== 'Tab') return
    const el = panelRef.current
    const items = [...el.querySelectorAll(FOCUSABLE)].filter((n) => n.offsetParent !== null)
    if (!items.length) { e.preventDefault(); return }
    const first = items[0]
    const last = items[items.length - 1]
    if (e.shiftKey && (document.activeElement === first || document.activeElement === el)) {
      e.preventDefault(); last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus()
    }
  }
  const body = typeof children === 'function' ? children(current) : children
  const panel = (
    <aside ref={panelRef} role="dialog" aria-labelledby={titleId} tabIndex={-1}
           className={['side-panel', mode === 'beside' ? 'beside' : 'over', className]
             .filter(Boolean).join(' ')}
           style={{ '--side-w': `${width}px` }} onKeyDown={onKeyDown}>
      <div className="side-panel-head">
        <h2 id={titleId}>{title}</h2>
        <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
          <UiGlyph name="close" size={18} />
        </button>
      </div>
      {tabs?.length > 0 && (
        <Tabs className="side-panel-tabs" items={tabs} value={current} onChange={setTab}
              label={title} />
      )}
      <div className="side-panel-body">{body}</div>
      {footer && <div className="side-panel-foot">{footer}</div>}
    </aside>
  )
  return mode === 'beside' ? panel : createPortal(panel, document.body)
}
