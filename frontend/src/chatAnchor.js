// "Open the chat AT this message": a deep-search hit (ChatGroups) or a link
// /c/<id>?msg=<message id>&q=<text> (routes.jsx OpenChat) leaves an anchor;
// Chat.jsx, once that chat's transcript is drawn, scrolls to the message (to
// the matching text inside it when it can find it), flashes it, and forgets
// the anchor. The same idea as ?step=<n> for a tool step.
const KEY = 'jarvis.chat.msg'

export function anchorValue(cid, mid, q = '') {
  return JSON.stringify({ cid: Number(cid), mid: Number(mid), q: String(q || '').slice(0, 200) })
}

// The anchor if it is for this chat, else null (junk, another chat, no id).
export function readAnchor(raw, cid) {
  if (!raw) return null
  try {
    const a = JSON.parse(raw)
    if (!a || Number(a.cid) !== Number(cid) || !Number.isInteger(a.mid) || a.mid <= 0) return null
    return { cid: Number(a.cid), mid: a.mid, q: typeof a.q === 'string' ? a.q : '' }
  } catch { return null }
}

export function setMsgAnchor(cid, mid, q) {
  try { sessionStorage.setItem(KEY, anchorValue(cid, mid, q)) } catch { /* private mode */ }
}
export function peekMsgAnchor(cid) {
  try { return readAnchor(sessionStorage.getItem(KEY), cid) } catch { return null }
}
export function clearMsgAnchor() {
  try { sessionStorage.removeItem(KEY) } catch { /* private mode */ }
}

// What to look for in the message's text: the whole query first, then its
// longest word (markdown may split a phrase across elements). Lowercased.
export function needles(q) {
  const whole = String(q || '').trim().toLowerCase()
  if (!whole) return []
  const words = whole.split(/\s+/).filter((w) => w.length >= 2)
    .sort((a, b) => b.length - a.length)
  return [whole, ...(words.length > 1 ? [words[0]] : [])]
}

// The first text range in `root` that holds a needle, or null. DOM only.
export function findTextRange(root, q) {
  if (!root || typeof document === 'undefined') return null
  for (const n of needles(q)) {
    const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    for (let t = walk.nextNode(); t; t = walk.nextNode()) {
      const i = t.data.toLowerCase().indexOf(n)
      if (i < 0) continue
      const r = document.createRange()
      r.setStart(t, i)
      r.setEnd(t, i + n.length)
      return r
    }
  }
  return null
}
