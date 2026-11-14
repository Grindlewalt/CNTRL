// The pure half of components/Select.jsx: no React, no DOM, so node can test
// it (src/components/__tests__/selectLogic.test.mjs).
//
// An option, once normalised, is { value, label, sub, disabled, group }:
// `value` is always a string, because that is what a native <select> hands to
// onChange (e.target.value) and what every call site already parses from.

export const SEARCH_AFTER = 6      // a search field shows above this many options
export const MAX_RECENT = 9        // number keys 1 to 9
export const RECENT_STORE = 'cntrl.select.recents'
export const MAX_RECENT_KEYS = 80  // distinct recentKeys kept in localStorage

export function normalizeOption(o, group = null) {
  if (o == null || o === false) return null
  if (typeof o !== 'object') {
    const s = String(o)
    return { value: s, label: s, sub: null, disabled: false, group }
  }
  const value = o.value == null ? '' : String(o.value)
  return {
    value,
    label: o.label == null ? value : o.label,
    sub: o.sub ?? null,
    disabled: !!o.disabled,
    group: o.group ?? group,
  }
}

// `placeholder` is the app's disabled-empty-option idiom; it is never listed
// as a choice, only shown in the trigger while the value is ''. A disabled
// option whose value is '' (written as <option value="" disabled>) is the
// same thing spelled as a child, so it becomes the placeholder too.
export function buildOptions(options = [], extra = [], placeholder = null) {
  let ph = placeholder ?? null
  const list = []
  for (const raw of [...options, ...extra]) {
    const o = normalizeOption(raw)
    if (!o) continue
    if (o.disabled && o.value === '') { if (ph == null) ph = textOf(o.label); continue }
    list.push(o)
  }
  return { list, placeholder: ph }
}

// Words in a label can be React nodes (a child <option> with markup); search
// and the trigger's sizer want plain text.
export function textOf(node) {
  if (node == null || node === false || node === true) return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (typeof node === 'object' && node.props) return textOf(node.props.children)
  return ''
}

const fold = (s) => String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()

// Every whitespace-separated term must appear in the label, value, sub line
// or group name. Matches whose label starts with the query come first, then
// labels with a word starting with it, then the rest, each in list order.
export function filterOptions(list, query) {
  const q = fold(query || '').trim()
  if (!q) return list
  const terms = q.split(/\s+/)
  const scored = []
  list.forEach((o, i) => {
    const label = fold(textOf(o.label))
    const hay = `${label} ${fold(o.value)} ${fold(textOf(o.sub))} ${fold(o.group || '')}`
    if (!terms.every((t) => hay.includes(t))) return
    const score = label.startsWith(q) ? 0
      : label.split(/[\s\-_/.(),]+/).some((w) => w.startsWith(terms[0])) ? 1 : 2
    scored.push({ o, i, score })
  })
  scored.sort((a, b) => a.score - b.score || a.i - b.i)
  return scored.map((s) => s.o)
}

// Most recent first, no duplicates, at most `max`.
export function pushRecent(recents, value, max = MAX_RECENT) {
  const v = String(value)
  return [v, ...(recents || []).filter((r) => r !== v)].slice(0, max)
}

// The recents that are still choices here, as options, most recent first.
export function recentOptions(list, recents, max = MAX_RECENT) {
  const by = new Map(list.filter((o) => !o.disabled).map((o) => [o.value, o]))
  const out = []
  for (const v of recents || []) {
    const o = by.get(v)
    if (o && !out.includes(o)) out.push(o)
    if (out.length >= max) break
  }
  return out
}

// What the open list shows, as sections ({ label, items }) of items
// ({ ...option, num }), plus the flat item list the keyboard walks.
//  - Searching: one section, the filtered options, no numbers (digits type).
//  - A long list (more than SEARCH_AFTER) with recents: "Recent" first,
//    numbered 1 to 9, then everything else in its own order and groups.
//  - A short list keeps the call site's order (often a meaning, like ask /
//    allow / off) and numbers by position, so 1 to 6 still pick.
export function arrange(list, recents, query = '') {
  const sections = []
  if ((query || '').trim()) {
    const items = filterOptions(list, query).map((o) => ({ ...o, num: null }))
    sections.push({ label: null, items })
  } else if (list.length > SEARCH_AFTER) {
    const rec = recentOptions(list, recents)
    if (rec.length) {
      sections.push({ label: 'Recent', items: rec.map((o, i) => ({ ...o, num: i + 1 })) })
    }
    const taken = new Set(rec)
    pushGroups(sections, list.filter((o) => !taken.has(o)).map((o) => ({ ...o, num: null })))
  } else {
    pushGroups(sections, list.map((o, i) => ({ ...o, num: i < MAX_RECENT ? i + 1 : null })))
  }
  return { sections, items: sections.flatMap((s) => s.items) }
}

// consecutive options of one group form one section
function pushGroups(sections, items) {
  let cur = null
  for (const it of items) {
    if (!cur || cur.label !== (it.group || null)) {
      cur = { label: it.group || null, items: [] }
      sections.push(cur)
    }
    cur.items.push(it)
  }
}

// The item a digit key picks, or null. Only enabled, numbered items.
export function pickByNumber(items, key) {
  const n = Number(key)
  if (!Number.isInteger(n) || n < 1 || n > MAX_RECENT) return null
  return items.find((it) => it.num === n && !it.disabled) || null
}

// What the trigger shows, as a native <select> would: the matching option;
// else the placeholder while the value is ''; else the first enabled option
// (React selects that one when the value matches nothing); else nothing.
export function displayFor(list, value, placeholder) {
  const v = value == null ? '' : String(value)
  const hit = list.find((o) => o.value === v)
  if (hit) return { option: hit, placeholder: false }
  if (v === '' && placeholder != null) return { option: null, placeholder: true }
  const first = list.find((o) => !o.disabled)
  if (first) return { option: first, placeholder: false }
  return { option: null, placeholder: placeholder != null }
}

// Arrow-key movement over the flat items, skipping disabled ones. No wrap:
// the ends stay put, as a listbox does.
export function step(items, from, delta) {
  if (!items.length) return -1
  const dir = delta < 0 ? -1 : 1
  if (from < 0 || from >= items.length) return firstEnabled(items, dir < 0)
  let left = Math.abs(delta)
  let i = from
  let last = from
  while (left > 0) {
    i += dir
    if (i < 0 || i >= items.length) break
    if (!items[i].disabled) { last = i; left -= 1 }
  }
  return items[last].disabled ? firstEnabled(items, dir < 0) : last
}

export function firstEnabled(items, fromEnd = false) {
  const idx = [...items.keys()]
  if (fromEnd) idx.reverse()
  for (const i of idx) if (!items[i].disabled) return i
  return -1
}

// Native typeahead for a list with no search field: the next enabled option
// after `from` whose label starts with the typed text.
export function typeahead(items, text, from = -1) {
  const t = fold(text)
  if (!t) return -1
  const n = items.length
  for (let k = 1; k <= n; k++) {
    const i = (from + k + n) % n
    const it = items[i]
    if (!it.disabled && fold(textOf(it.label)).startsWith(t)) return i
  }
  return -1
}

// ---- recents in localStorage ----
// One key holds every list's recents: { [recentKey]: [value, ...] }, the most
// recently used list last, capped at MAX_RECENT_KEYS lists. Storage can be
// missing or throw (a private window, a full quota); recents are a
// convenience, so every failure reads as "no recents".
export function readRecents(storage, key) {
  if (!key) return []
  try {
    const all = JSON.parse(storage?.getItem(RECENT_STORE) || '{}')
    const got = all && typeof all === 'object' ? all[key] : null
    return Array.isArray(got) ? got.map(String).slice(0, MAX_RECENT) : []
  } catch { return [] }
}

export function writeRecent(storage, key, value) {
  if (!key || !storage) return []
  try {
    let all
    try { all = JSON.parse(storage.getItem(RECENT_STORE) || '{}') } catch { all = null }
    if (!all || typeof all !== 'object' || Array.isArray(all)) all = {}
    const next = pushRecent(Array.isArray(all[key]) ? all[key] : [], value)
    delete all[key]
    all[key] = next
    const keys = Object.keys(all)
    for (const k of keys.slice(0, Math.max(0, keys.length - MAX_RECENT_KEYS))) delete all[k]
    storage.setItem(RECENT_STORE, JSON.stringify(all))
    return next
  } catch { return [] }
}
