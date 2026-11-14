import { useCallback, useEffect, useRef, useState } from 'react'
import Menu, { MenuItem, MenuSep } from './Menu.jsx'
import { UiGlyph } from '../shell/glyphs.jsx'

// A row's actions: one menu, two ways in. The ⋯ button opens it at the
// button; a right-click anywhere on the row opens the same menu at the
// cursor. Shift+right-click still gives the browser's own menu (copy a link,
// inspect). The ContextMenu key or Shift+F10 on a focused row opens it at the
// button, and the menu takes the focus so arrows walk it.
//
//   function ChatRow({ chat }) {
//     const { rowProps, menu, open } = useRowMenu([
//       { label: chat.starred ? 'Unstar' : 'Star', onSelect: () => star(chat) },
//       { label: 'Rename', onSelect: () => rename(chat) },
//       '-',
//       { label: 'Delete', danger: true, onSelect: () => remove(chat) },
//     ], { label: 'Chat actions' })
//     return <li {...rowProps} className={open ? 'menu-open' : ''}>{chat.title}{menu}</li>
//   }
//
// Items: { label, onSelect, danger?, disabled?, checked?, sub?, keepOpen? };
// '-' (or { separator: true }) for a hairline; { caption } for a small group
// heading. Falsy entries are skipped, so `cond && {...}` works. The menu
// closes before onSelect runs, so an onSelect that opens a dialog is not
// fighting the menu for focus.
//
// Clicks inside the menu or on ⋯ do not reach the row (a row is usually a
// button that opens something). rowProps carries onContextMenu and onKeyDown:
// spread it before any onKeyDown of the row's own, or call both.

function renderItems(items, onClose) {
  return items.filter(Boolean).map((it, i) => {
    if (it === '-' || it.separator) return <MenuSep key={`s${i}`} />
    if (it.caption) return <div key={`c${i}`} className="menu-caption">{it.caption}</div>
    return (
      <MenuItem key={`i${i}`} danger={!!it.danger} disabled={it.disabled} checked={it.checked}
                sub={it.sub}
                onClick={() => { if (!it.keepOpen) onClose(); it.onSelect?.() }}>
        {it.label}
      </MenuItem>
    )
  })
}

// The ⋯ button and its menu, controlled. useRowMenu wires it for one row; a
// list that keeps one "which row's menu is open" state can use it directly:
//   <RowMenu items={items} open={menu?.id === row.id} at={menu?.at}
//            onOpen={(at) => setMenu({ id: row.id, at })} onClose={() => setMenu(null)} />
// `at` is {x, y} in viewport pixels (a right-click) or null (at the button).
export function RowMenu({
  items, open, at = null, onOpen, onClose, label = 'Actions', width = 220,
  align = 'right', buttonClassName = 'win-btn', icon = null,
}) {
  const btnRef = useRef(null)
  const wasOpen = useRef(false)
  // focus back on ⋯ when the menu closes with the focus inside it (Escape, a
  // pick that opened nothing); a click elsewhere keeps the focus it made
  useEffect(() => {
    if (open) { wasOpen.current = true; return }
    if (!wasOpen.current) return
    wasOpen.current = false
    const ae = document.activeElement
    if (!ae || ae === document.body) btnRef.current?.focus({ preventScroll: true })
  }, [open])
  const stop = (e) => e.stopPropagation()
  const list = typeof items === 'function' ? items() : items
  return (
    <span className="row-menu" onClick={stop}
          onContextMenu={(e) => { e.preventDefault(); e.stopPropagation() }}>
      <Menu open={open} onClose={onClose} floating at={at} align={align} autoFocus
            width={width} label={label}
            trigger={(
              <button ref={btnRef} type="button" className={buttonClassName} title={label}
                      aria-label={label} aria-haspopup="menu" aria-expanded={open}
                      onClick={() => (open ? onClose() : onOpen(null))}>
                {icon || <UiGlyph name="more" size={15} />}
              </button>
            )}>
        {open && renderItems(list || [], onClose)}
      </Menu>
    </span>
  )
}

// One row's menu state. Returns:
//   rowProps   spread on the row: right-click and the ContextMenu key
//   menu       the <RowMenu/> element (the ⋯ button and its menu) to render
//              inside the row
//   open       whether it is open (style the row: .menu-open)
//   openAt(at) / close()   to drive it from elsewhere
// opts: label (the button's name and the menu's), width, align,
// buttonClassName, icon.
export function useRowMenu(items, opts = {}) {
  const [state, setState] = useState(null)        // null, or { at: {x,y} | null }
  const openAt = useCallback((at = null) => setState({ at }), [])
  const close = useCallback(() => setState(null), [])
  const open = state !== null
  const rowProps = {
    onContextMenu: (e) => {
      if (e.shiftKey) return
      e.preventDefault()
      setState({ at: { x: e.clientX, y: e.clientY } })
    },
    onKeyDown: (e) => {
      if (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) {
        e.preventDefault()
        setState({ at: null })
      }
    },
  }
  const menu = (
    <RowMenu items={items} open={open} at={state?.at || null} onOpen={openAt}
             onClose={close} {...opts} />
  )
  return { rowProps, menu, open, openAt, close }
}

export default RowMenu
