import { useEffect, useRef } from 'react'
import { useDismiss } from '../useDismiss.js'
import Button from './Button.jsx'
import HelpTip from './HelpTip.jsx'

// The dialog: scrim, paper box, head / body / foot. Used for confirmations
// and for form editors (profiles, secrets, instructions).
//
//   <Modal title="Edit profile" onClose={close} onSubmit={save}
//          submitLabel="Save" submitDisabled={!dirty} help="Applies to every project on it.">
//     <Input label="Name" value={name} onChange={...} />
//     ...a long form scrolls here, between a fixed head and foot...
//   </Modal>
//
// Props:
//   open (default true), onClose     Escape, the × and (unless closeOnScrim is
//                                    false) a click on the scrim call onClose
//   title, help                      the heading, and an optional HelpTip beside it
//   onSubmit                         the box is a <form>: Enter in a field and the
//                                    submit button both call it (default prevented)
//   submitLabel                      with it and no `footer`, the foot is the
//                                    standard pair: Cancel (ghost) then the
//                                    primary submit, refuse before approve
//   cancelLabel ('Cancel'), submitDisabled, danger (a red submit), busy
//                                    (both buttons off while a save runs)
//   footerStart                      left side of the standard foot: a Delete
//                                    link, an error line
//   footer                           your own foot instead of the standard one
//   width (480), labelledBy, className, closeOnScrim (true; pass false for an
//   editor holding unsaved typing)
//
// Kept from the dialogs this replaced:
//  - the scrim sits at z-index 4000, above .notices (3000). A toast must never
//    be clickable through an open dialog: tapping one navigates away
//    mid-question.
//  - the body is the ONLY scroll container; head and foot stay put.
//  - ask.jsx focuses Cancel rather than Confirm on a destructive question, on
//    purpose. That is ask.jsx's policy, not every dialog's, so it stays there.
// Focus: the box takes focus on open (unless a field inside already has it,
// e.g. autoFocus), Tab and Shift+Tab cycle inside it, and focus returns to
// whatever had it before the dialog opened.
const NOOP = () => {}
const NEVER_OUTSIDE = { current: { contains: () => true } }
const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled):not([type="hidden"]), '
  + 'select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'

export default function Modal({
  open = true, onClose, title, help, width = 480, children, footer, footerStart,
  submitLabel, cancelLabel = 'Cancel', submitDisabled = false, danger = false, busy = false,
  labelledBy, className = '', onSubmit, closeOnScrim = true,
}) {
  // a stable fallback: a fresh arrow each render would make useDismiss tear
  // down and re-add its listeners on every render while the dialog is open
  const boxRef = useDismiss(open, onClose || NOOP, closeOnScrim ? null : NEVER_OUTSIDE)
  const back = useRef(null)

  // The page behind must not scroll while a dialog is up: on iOS the shell is
  // position:fixed already, but a desktop board keeps its scrollbar.
  useEffect(() => {
    if (!open) return undefined
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [open])

  useEffect(() => {
    if (!open) return undefined
    back.current = document.activeElement
    const box = boxRef.current
    if (box && !box.contains(document.activeElement)) box.focus({ preventScroll: true })
    return () => {
      const b = back.current
      if (b && b !== document.body && document.contains(b)) b.focus?.({ preventScroll: true })
    }
  }, [open, boxRef])

  if (!open) return null
  const submit = (e) => {
    e.preventDefault()
    if (onSubmit && !busy && !submitDisabled) onSubmit(e)
  }
  const onKeyDown = (e) => {
    if (e.key !== 'Tab') return
    const box = boxRef.current
    const items = [...box.querySelectorAll(FOCUSABLE)].filter((n) => n.offsetParent !== null)
    if (!items.length) { e.preventDefault(); return }
    const first = items[0]
    const last = items[items.length - 1]
    if (e.shiftKey && (document.activeElement === first || document.activeElement === box)) {
      e.preventDefault(); last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus()
    }
  }
  const foot = footer || (submitLabel && (
    <>
      {footerStart}
      {footerStart && <span className="grow" />}
      <Button variant="ghost" onClick={onClose} disabled={busy}>{cancelLabel}</Button>
      <Button type="submit" danger={danger} disabled={busy || submitDisabled}>{submitLabel}</Button>
    </>
  ))
  return (
    <div className="modal-scrim">
      <form
        ref={boxRef}
        className={['modal', className].filter(Boolean).join(' ')}
        style={{ '--modal-w': `${width}px` }}
        role="dialog"
        aria-modal="true"
        aria-label={labelledBy ? undefined : title}
        aria-labelledby={labelledBy}
        tabIndex={-1}
        onSubmit={submit}
        onKeyDown={onKeyDown}
      >
        {title && (
          <div className="modal-head">
            <h2>{title}{help && <HelpTip label={`About ${title}`}>{help}</HelpTip>}</h2>
            {onClose && (
              <Button variant="icon" onClick={onClose} aria-label="close">×</Button>
            )}
          </div>
        )}
        <div className="modal-body">{children}</div>
        {foot && <div className="modal-foot">{foot}</div>}
      </form>
    </div>
  )
}
