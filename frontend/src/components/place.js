// Where a portalled popover goes: fixed-positioned from an anchor rect (a
// trigger's getBoundingClientRect(), or a zero-size rect at the cursor for a
// right-click menu), clamped inside the viewport with a 16px gutter, and
// flipped above the anchor when there is more room there. Menu (floating),
// Select and HelpTip all place their popovers with this.
export const GUTTER = 16

export function pointRect(x, y) {
  return { left: x, right: x, top: y, bottom: y, width: 0, height: 0 }
}

// align: 'left' puts the popover's left edge on the anchor's left edge,
// 'right' its right edge on the anchor's right edge. `up` prefers above.
// Sets left/top/max-height inline and data-side="up"|"down" (not className:
// React owns that).
export function placePopover(pop, rect, { align = 'right', up = false, offset = 8 } = {}) {
  const vw = document.documentElement.clientWidth
  const vh = window.innerHeight
  const w = pop.offsetWidth
  let left = align === 'left' ? rect.left : rect.right - w
  left = Math.max(GUTTER, Math.min(left, vw - GUTTER - w))
  pop.style.maxHeight = 'none'
  const h = pop.offsetHeight
  const below = vh - rect.bottom - offset - GUTTER
  const above = rect.top - offset - GUTTER
  const openUp = up ? (above >= h || above > below) : (below < h && above > below)
  const room = Math.max(120, openUp ? above : below)
  const shown = Math.min(h, room)
  pop.style.left = `${left}px`
  pop.style.maxHeight = `${room}px`
  pop.style.top = `${openUp ? rect.top - offset - shown : rect.bottom + offset}px`
  pop.dataset.side = openUp ? 'up' : 'down'
}

// A pointerdown inside a portalled popover must not reach the document
// listeners of the layers under it: a Select open inside a Modal is outside
// the Modal's DOM, so the Modal's useDismiss would read a click on an option
// as a click on the scrim and close the dialog. React listens on the portal
// container (<body>), so stopping it here stops it before `document`.
export const keepInside = (e) => e.stopPropagation()
