import { useEffect, useLayoutEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { NavLink } from 'react-router-dom'
import { useDismiss } from '../useDismiss.js'
import { keepInside, placePopover, pointRect } from './place.js'

// The dropdown. A paper panel, 14px corners, a 6px inset, --shadow-pop,
// dropping in from its own corner. `.menu` in styles.css is that recipe once;
// .notif-drop and .proj-menu keep their classes until their call sites migrate.
//
// The caller keeps the open state and renders its own trigger, because every
// trigger in the app is styled for where it sits (.nav-more, .proj-chip,
// .model-chip, a .win-btn on a card row). What they share is the wrapper, the
// popover and its dismissal: Escape and click-away come from useDismiss, whose
// ref has to wrap the trigger AND the menu together — a pointerdown on the
// trigger must not count as "outside", or a toggle would close-then-reopen.
//
// `up` opens above the trigger (the model picker lives in a bar at the bottom
// of the screen); `align` picks the anchored edge, 'right' like every existing
// menu. The trigger should carry aria-haspopup="menu" and aria-expanded.
//
// `floating` is for a menu whose trigger sits inside something that clips —
// a Workspace panel (overflow:hidden), a card near the screen edge. The
// popover is portalled to <body>, fixed-positioned from the trigger's rect and
// clamped inside the viewport with a 16px gutter, flipping above the trigger
// when there is more room there (components/place.js). The nav's menus keep
// the in-place absolute recipe: their rules position them against the bar and
// the rail.
//
// `at` ({x, y}, viewport pixels) anchors a floating menu at a point instead of
// the trigger: a right-click menu opens at the cursor (RowMenu). It implies
// `floating`.
//
// Keyboard: ArrowUp/ArrowDown/Home/End move between the items; `autoFocus`
// focuses the first item when the menu opens (RowMenu does, so the keyboard
// is already in the menu).
const NOOP = () => {}
const ITEMS = '[role^="menuitem"]:not(:disabled):not([aria-disabled="true"])'

function moveFocus(e) {
  const keys = { ArrowDown: 1, ArrowUp: -1, Home: 'first', End: 'last' }
  const k = keys[e.key]
  if (!k) return
  const items = [...e.currentTarget.querySelectorAll(ITEMS)]
  if (!items.length) return
  e.preventDefault()
  const i = items.indexOf(document.activeElement)
  let next
  if (k === 'first') next = 0
  else if (k === 'last') next = items.length - 1
  else if (i < 0) next = k > 0 ? 0 : items.length - 1
  else next = Math.max(0, Math.min(items.length - 1, i + k))
  items[next].focus()
}

export default function Menu({
  open, onClose = NOOP, trigger, align = 'right', up = false, width,
  label, className = '', wrapClassName = '', floating = false, at = null,
  autoFocus = false, children,
}) {
  const popRef = useRef(null)
  const float = floating || !!at
  const ref = useDismiss(open, onClose, float ? popRef : null)
  const cls = ['menu', up ? 'up' : '', align === 'left' ? 'left' : '',
               float ? 'floating' : '', className].filter(Boolean).join(' ')
  const x = at?.x
  const y = at?.y

  // before paint, so the menu never flashes at its unplaced spot; a scroll
  // anywhere (the board, a panel, the page) or a resize moves the trigger
  useLayoutEffect(() => {
    if (!open || !float) return undefined
    const run = () => {
      if (!popRef.current) return
      if (x != null) placePopover(popRef.current, pointRect(x, y), { align: 'left', up, offset: 2 })
      else if (ref.current) placePopover(popRef.current, ref.current.getBoundingClientRect(), { align, up })
    }
    run()
    window.addEventListener('resize', run)
    window.addEventListener('scroll', run, true)
    return () => {
      window.removeEventListener('resize', run)
      window.removeEventListener('scroll', run, true)
    }
  }, [open, float, align, up, ref, x, y])

  useEffect(() => {
    if (open && autoFocus) popRef.current?.querySelector(ITEMS)?.focus()
  }, [open, autoFocus, x, y])

  const pop = open && (
    <div ref={popRef} className={cls} role="menu" aria-label={label}
         onKeyDown={moveFocus} onPointerDown={float ? keepInside : undefined}
         style={width ? { '--menu-w': `${width}px` } : undefined}>
      {children}
    </div>
  )
  return (
    <div className={['menu-wrap', wrapClassName].filter(Boolean).join(' ')} ref={ref}>
      {trigger}
      {float ? (pop && createPortal(pop, document.body)) : pop}
    </div>
  )
}

// One row. A button by default; a router link when `to` is given (the nav's
// overflow items), which NavLink marks `.active` on the current route. `sub` is
// the second, dimmer line the project and model pickers put under a name.
// `checked` renders the accent dot those pickers use and switches the role to
// menuitemradio, so a screen reader hears which one is chosen — pass it as a
// boolean on every item of a radio group, not only the chosen one.
export function MenuItem({
  to, end, sub, checked, danger = false, className = '', children, ...rest
}) {
  const cls = ['menu-item', danger ? 'danger' : '', className].filter(Boolean).join(' ')
  const body = (
    <>
      {sub != null
        ? <span className="m-name">{children}<span className="m-sub">{sub}</span></span>
        : children}
      {checked && <span className="m-check" aria-hidden="true">●</span>}
    </>
  )
  if (to) {
    return (
      <NavLink to={to} end={end} role="menuitem" className={cls} {...rest}>
        {body}
      </NavLink>
    )
  }
  const radio = checked !== undefined
  return (
    <button type="button" className={cls}
            role={radio ? 'menuitemradio' : 'menuitem'}
            aria-checked={radio ? !!checked : undefined} {...rest}>
      {body}
    </button>
  )
}

// The hairline between groups (.proj-sep, between the binding modes and the
// project list in the composer's picker).
export function MenuSep() { return <div className="menu-sep" role="separator" /> }
