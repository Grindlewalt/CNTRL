import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useDismiss } from '../useDismiss.js'
import { keepInside, placePopover } from './place.js'

// The explanation behind a `?`. Settings and editors show one short line at
// most; the rest goes here.
//
//   <span>Network <HelpTip>A site on neither list waits for you.</HelpTip></span>
//
// Hover (or keyboard focus) shows it, a click pins it open until a click
// elsewhere, Escape closes it. The `?` is a real button, so Tab reaches it.
// Props: children (the text, or a few short lines), label (the button's name,
// default "More about this"; say what it explains: "About network modes"),
// align ('left' | 'right', which edge of the popover lines up with the `?`),
// width (px, default 280), className.
const SHOW_MS = 120
const HIDE_MS = 160

export default function HelpTip({
  children, label = 'More about this', align = 'left', width = 280, className = '',
}) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const btnRef = useRef(null)
  const popRef = useRef(null)
  const timer = useRef(null)
  const close = () => { clearTimeout(timer.current); setOpen(false); setPinned(false) }
  const wrapRef = useDismiss(open, close, popRef)

  const later = (fn, ms) => { clearTimeout(timer.current); timer.current = setTimeout(fn, ms) }
  const hoverIn = () => { if (!pinned) later(() => setOpen(true), SHOW_MS) }
  const hoverOut = () => { if (!pinned) later(() => setOpen(false), HIDE_MS) }
  useEffect(() => () => clearTimeout(timer.current), [])

  useLayoutEffect(() => {
    if (!open) return undefined
    const run = () => {
      if (btnRef.current && popRef.current) {
        placePopover(popRef.current, btnRef.current.getBoundingClientRect(), { align, offset: 6 })
      }
    }
    run()
    window.addEventListener('resize', run)
    window.addEventListener('scroll', run, true)
    return () => {
      window.removeEventListener('resize', run)
      window.removeEventListener('scroll', run, true)
    }
  }, [open, align])

  return (
    <span ref={wrapRef} className={['help-tip', className].filter(Boolean).join(' ')}
          onMouseEnter={hoverIn} onMouseLeave={hoverOut}>
      <button ref={btnRef} type="button" className="help-tip-btn" aria-label={label}
              aria-expanded={open} aria-describedby={open ? id : undefined}
              onFocus={(e) => { if (e.target.matches(':focus-visible')) setOpen(true) }}
              onBlur={() => { if (!pinned) setOpen(false) }}
              onClick={(e) => {
                e.preventDefault()
                e.stopPropagation()
                if (pinned) close()
                else { clearTimeout(timer.current); setOpen(true); setPinned(true) }
              }}>?</button>
      {open && createPortal(
        <div ref={popRef} id={id} role="tooltip" className="help-tip-pop"
             style={{ '--tip-w': `${width}px` }} onPointerDown={keepInside}
             onMouseEnter={hoverIn} onMouseLeave={hoverOut}>
          {children}
        </div>, document.body)}
    </span>
  )
}
