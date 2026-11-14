// Pure helpers for pages/ProjectHome.jsx and the /project slash command. No
// DOM and no imports, so node can test them (pages/__tests__/projectHome.test.mjs).

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

// SQLite's datetime('now') is UTC with no zone marker, so a bare
// "YYYY-MM-DD HH:MM:SS" is read as UTC (as format.js `ago` does).
export function parseStamp(s) {
  if (!s) return null
  const iso = String(s).trim().replace(' ', 'T')
  const t = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`)
  return Number.isNaN(t) ? null : t
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'} ago`

// A chat's date on the Recents list, by the reader's calendar: "Just now",
// "5 minutes ago" and "9 hours ago" today, "Yesterday", then "Oct 30" this
// year and "Oct 30, 2025" before it. The last hour reads in minutes even
// across midnight.
export function relativeDay(s, now = new Date()) {
  const t = parseStamp(s)
  if (t == null) return ''
  const d = new Date(t)
  const sec = Math.max(0, (now.getTime() - t) / 1000)
  if (sec < 3600 || d.toDateString() === now.toDateString()) {
    if (sec < 60) return 'Just now'
    if (sec < 3600) return plural(Math.floor(sec / 60), 'minute')
    return plural(Math.floor(sec / 3600), 'hour')
  }
  const y = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)
  if (d.toDateString() === y.toDateString()) return 'Yesterday'
  const md = `${MONTHS[d.getMonth()]} ${d.getDate()}`
  return d.getFullYear() === now.getFullYear() ? md : `${md}, ${d.getFullYear()}`
}

// 950 -> "950", 1234 -> "1.2k", 12000 -> "12k"
export function fmtTokens(n) {
  const v = Number(n) || 0
  if (v < 1000) return String(v)
  return `${(v / 1000).toFixed(1).replace(/\.0$/, '')}k`
}

// the backend's estimate (memory.estimate_tokens): chars / 4
export const estimateTokens = (text) => Math.max(0, Math.round(String(text || '').length / 4))

// What the Recents list calls a chat (the sidebar's rule, ChatGroups listTitle).
export function chatTitle(c) {
  const s = String(c?.summary || '').replace(/^[#[\s]+/, '')
  if (s) return s
  if (c?.summary) return c.summary
  return `#${c?.id}, ${String(c?.started_at || '').slice(5, 16)}`
}

const PLACEHOLDER = "(describe what you're building here)"

// The project's one-line description: the first paragraph under "## Summary"
// in project.md, else the first paragraph that is not a heading. Nothing while
// it is still the new-project placeholder.
export function summaryOf(md) {
  const text = String(md || '')
  const sec = /^##\s+Summary\s*$/im.exec(text)
  const from = sec ? text.slice(sec.index + sec[0].length) : text
  const para = from.split(/\n\s*\n/).map((b) => b.trim())
    .find((b) => b && !b.startsWith('#')) || ''
  const one = para.replace(/\s+/g, ' ').trim()
  return one === PLACEHOLDER ? '' : one
}

// The first line of some text, cut at `max` characters.
export function firstLine(text, max = 140) {
  const line = String(text || '').split('\n').map((l) => l.trim()).find(Boolean) || ''
  return line.length > max ? `${line.slice(0, max - 1)}…` : line
}

// "Daily at 09:00", "Every 6 hours", "Every 45 minutes"
export function cadenceLabel(s) {
  if (s?.cadence_kind === 'daily') return `Daily at ${s.daily_at || '09:00'}`
  const m = Number(s?.interval_minutes) || 0
  if (m >= 60 && m % 60 === 0) {
    const h = m / 60
    return h === 1 ? 'Every hour' : `Every ${h} hours`
  }
  return `Every ${m} minutes`
}

// /project <name>: the project a typed word means. An exact slug, then a name
// in any case, then a unique prefix of either. null when nothing matches or a
// prefix matches more than one project.
export function findProject(projects, arg) {
  const want = String(arg || '').trim().toLowerCase()
  if (!want) return null
  const list = projects || []
  const exact = list.find((p) => p.slug === want)
    || list.find((p) => String(p.name || '').toLowerCase() === want)
  if (exact) return exact
  const hits = list.filter((p) => p.slug.startsWith(want)
    || String(p.name || '').toLowerCase().startsWith(want))
  return hits.length === 1 ? hits[0] : null
}
