// The Security dashboard's pure parts: every pending request, question and alert
// as one kind of row (what it is, for which project, the agent's two-sentence
// summary or the best short reason there is), the words on its buttons, and the
// boxes at a glance. SecurityDash.jsx only draws them.
//
// Every string taken from a row (summaries, reasons, commit messages, hosts,
// package names, questions) was written by an agent, a box or the internet:
// nothing here builds markup, and the components place it as text nodes.

import { cardTitle, countsLine, lineTitle } from './securityRuns.js'
import { doingNow, activityWord } from './boxes/glance.js'
import { verLabel } from './boxes/logic.js'

const firstLine = (s) => String(s ?? '').split('\n').map((l) => l.trim()).find(Boolean) || ''
const oneLine = (s) => String(s ?? '').replace(/\s+/g, ' ').trim()

// What a git request is: a commit, a pull request (push), a remote, or the
// orchestrator's merge
export function gitKind(r) {
  return ['push', 'remote', 'merge'].includes(r?.kind) ? r.kind : 'commit'
}

const GIT_LABEL = { commit: 'Commit', push: 'Pull request', remote: 'New remote', merge: 'Merge' }

// The agent's summary, else the best short reason the request carries, as
// {summary, byAgent}. byAgent says which, so the detail can label it.
export function summaryOf(kind, r = {}) {
  const own = oneLine(r.agent_summary)
  if (own) return { summary: own, byAgent: true }
  let why = ''
  if (kind === 'git') {
    const g = gitKind(r)
    why = g === 'remote'
      ? `Connect ${r.message} as this project's remote and push its commits.`
      : firstLine(r.message)
  } else if (kind === 'service') why = firstLine(r.description) || firstLine(r.reason)
  else if (kind === 'page') why = `Publish ${r.name} at a link anyone can open.`
  else if (kind === 'journal') why = oneLine(r.text)
  else if (kind === 'site') {
    why = r.refused ? `It cannot be allowed: ${r.refused}.`
      : oneLine(r.triage_reason) || oneLine(r.auto_reason)
        || 'A box tried to reach it and no list covers it, so it was blocked.'
  } else if (kind === 'ask') why = oneLine(r.question)
  else why = firstLine(r.reason)
  return { summary: why, byAgent: false }
}

const pkgName = (p) => `${p.package}${(p.resolved_version || p.version_req)
  ? ` ${p.resolved_version || p.version_req}` : ''}`

// One row: {key, kind, sub, id, label, project, chat, at, subject, mono,
// summary, byAgent, tone, raw}. `kind` picks the detail and the actions.
export function rowFor(kind, r) {
  const base = { kind, raw: r, tone: 'warn', mono: false }
  const { summary, byAgent } = summaryOf(kind, r)
  switch (kind) {
    case 'git': {
      const sub = gitKind(r)
      return { ...base, key: `git:${r.slug || r.project_slug}:${r.id}`, sub, id: r.id,
        label: GIT_LABEL[sub], project: r.slug || r.project_slug, chat: r.conversation_id || null,
        at: r.created_at, subject: sub === 'remote' ? r.message : firstLine(r.message),
        mono: sub === 'remote', summary, byAgent, tone: r.error ? 'bad' : 'warn' }
    }
    case 'service':
      return { ...base, key: `service:${r.id}`, id: r.id,
        label: r.supersedes_id ? 'Service update' : 'Service', project: r.project_slug,
        chat: r.conversation_id || null, at: r.created_at, subject: r.name, mono: true,
        summary, byAgent }
    case 'package':
      return { ...base, key: `package:${r.id}`, id: r.id, label: `${r.manager} package`,
        project: r.project_slug, chat: r.conversation_id || null, at: r.created_at,
        subject: pkgName(r), mono: true, summary, byAgent }
    case 'environment':
      return { ...base, key: `environment:${r.id}`, id: r.id, sub: r.kind === 'use' ? 'use' : 'build',
        label: r.kind === 'use' ? 'Image change' : 'New environment', project: r.project_slug,
        chat: r.conversation_id || null, at: r.created_at, subject: r.name, mono: true,
        summary, byAgent }
    case 'skill':
      return { ...base, key: `skill:${r.id}`, id: r.id, label: 'Skill',
        project: r.project_slug, chat: r.conversation_id || null, at: r.created_at,
        subject: r.skill_name || r.name || r.source, mono: true, summary, byAgent }
    case 'journal':
      return { ...base, key: `journal:${r.project}:${r.id}`, id: r.id, label: 'Journal entry',
        project: r.project, chat: null, at: r.date || null, subject: '', summary, byAgent }
    case 'page':
      return { ...base, key: `page:${r.id}`, id: r.id, label: 'Page to publish',
        project: r.project, chat: r.conversation_id || null, at: r.created_at,
        subject: r.name, summary, byAgent }
    case 'site':
      return { ...base, key: `site:${r.id}`, id: r.id, label: 'Site',
        project: r.project_slug || r.project || null, chat: null,
        at: r.last_seen || r.created_at || r.first_seen, subject: r.host, mono: true, summary, byAgent,
        tone: r.refused ? 'bad' : (r.triage_verdict === 'flag' || r.auto_verdict === 'unsure') ? 'warn' : 'quiet' }
    case 'ask':
      return { ...base, key: `ask:${r.id}`, id: r.id,
        label: r.kind === 'permission' ? 'Permission' : 'Question',
        project: r.project || r.project_slug || null, chat: r.conversation_id || null,
        at: null, ageS: r.age_s, subject: '', summary, byAgent }
    default:
      return null
  }
}

// An alert card (one run) as a row. A run whose waiting events are all info,
// or only the agents' own reports, is `quiet`: it folds under the list.
export function alertRow(run) {
  const kinds = run?.kinds || []
  const sev = kinds.map((k) => String(k.severity || 'info').toLowerCase())
  const tone = sev.some((s) => s === 'critical' || s === 'crit') ? 'bad'
    : sev.some((s) => s === 'warn' || s === 'warning') ? 'warn' : 'quiet'
  const need = run?.counts?.need || 0
  const reports = run?.counts?.reports || 0
  const newest = (run?.events || [])[0]
  return {
    kind: 'alert', key: `alert:${run.key}`, id: run.key, raw: run,
    label: need ? (need === 1 ? 'Alert' : `${need} alerts`) : (reports === 1 ? 'Agent report' : `${reports} agent reports`),
    project: run.project || null, chat: null, at: run.last_at || null,
    subject: cardTitle(run), mono: false,
    summary: newest ? oneLine(lineTitle(newest)) : countsLine(run.counts),
    byAgent: false, tone, quiet: tone === 'quiet',
  }
}

// A state worth a word on the row, as {tone, word, why} or null: a failed
// attempt, a site that can never be allowed, Auto review's flag, an alert's
// severity. Ordinary waiting needs no word: being in the list says it.
export function rowState(row) {
  const r = row?.raw || {}
  if (row?.kind === 'git' && r.error) return { tone: 'bad', word: 'Last attempt failed', why: r.error }
  if (row?.kind === 'site') {
    if (r.refused) return { tone: 'bad', word: 'Cannot be allowed', why: r.refused }
    if (r.triage_verdict === 'flag') return { tone: 'warn', word: 'Flagged by Auto review', why: r.triage_reason || '' }
    if (r.auto_verdict === 'unsure') return { tone: 'warn', word: 'Auto-allow was not sure', why: r.auto_reason || '' }
  }
  if ((row?.kind === 'package' || row?.kind === 'environment') && r.status === 'pending'
      && (row.kind === 'package' ? !r.resolved_version : (row.sub !== 'use' && !r.resolved))) {
    return { tone: 'warn', word: 'Not resolved yet', why: '' }
  }
  if (row?.kind === 'skill' && r.error) return { tone: 'bad', word: 'Error', why: r.error }
  if (row?.kind === 'alert') {
    if (row.tone === 'bad') return { tone: 'bad', word: 'Critical', why: '' }
    if (row.tone === 'warn') return { tone: 'warn', word: 'Warning', why: '' }
  }
  return null
}

// Newest first; a row with no time keeps its place after the timed ones.
const newestFirst = (a, b) => String(b.at || '').localeCompare(String(a.at || ''))

// Everything waiting, as {rows, quiet}: requests and questions first (newest
// first), then the alerts that need you; info-only alerts and agent reports
// apart in `quiet`, folded under the list.
export function queueRows(src = {}) {
  const req = []
  const push = (kind, list) => (list || []).forEach((r) => {
    const row = rowFor(kind, r)
    if (row) req.push(row)
  })
  push('git', src.git)
  push('service', src.services)
  push('package', src.packages)
  push('environment', src.environments)
  push('skill', src.skills)
  push('journal', src.journal)
  push('page', src.pages)
  push('site', src.sites)
  req.sort(newestFirst)
  const asks = (src.asks || []).map((a) => rowFor('ask', a))
  const alerts = (src.runs || []).map(alertRow).sort(newestFirst)
  return {
    rows: [...asks, ...req, ...alerts.filter((a) => !a.quiet)],
    quiet: alerts.filter((a) => a.quiet),
  }
}

// ---- the decision buttons ----------------------------------------------------
// The refusing verb, the approving verb, and whether approving needs the full
// request first (a form under Inspect: where a service runs, a skill to read).
// null = the kind has no such button.

export function denyLabel(row) {
  if (row.kind === 'ask' || row.kind === 'alert') return null
  if (row.kind === 'journal') return 'Drop'
  return 'Deny'
}

export function approveLabel(row) {
  const r = row.raw || {}
  switch (row.kind) {
    case 'git':
      if (r.error) return 'Retry'
      return row.sub === 'push' || row.sub === 'merge' ? 'Merge' : row.sub === 'remote' ? 'Connect' : 'Approve'
    case 'package': return 'Approve…'
    case 'service': return 'Approve…'
    case 'environment': return row.sub === 'use' ? 'Approve' : 'Approve and build'
    case 'skill': return r.status === 'fetched' ? 'Install and grant' : 'Fetch and read'
    case 'page': return 'Publish'
    case 'site': return 'Allow always'
    case 'ask': return 'Answer in chat'
    case 'alert': return 'Resolve'
    default: return 'Approve'
  }
}

// Approving it needs the full request on screen (a choice to make or text to
// read first): the Approve button opens Inspect instead of deciding.
export function approveOpensInspect(row) {
  const r = row.raw || {}
  if (row.kind === 'service') return true
  if (row.kind === 'environment') return row.sub !== 'use' && !r.resolved
  return false
}

// What approving changes, in a sentence (the old cards' words)
export function changeText(row) {
  const r = row.raw || {}
  switch (row.kind) {
    case 'git':
      if (row.sub === 'remote') return 'Connect checks the repository, connects it, and pushes the existing commits.'
      if (row.sub === 'push' || row.sub === 'merge') return 'Merge merges the pull request into main.'
      return 'Approve commits the changes to main, then pushes them if the project has a remote.'
    case 'package':
      return `Approve first lists every project on the ${r.target_variant || 'main'} image; `
        + 'nothing is installed until you confirm there.'
    case 'service':
      return 'Approve opens the full request, where you choose where it runs and which ports are reachable.'
    case 'environment':
      return row.sub === 'use' ? `Moves ${r.project_slug} onto image ${r.name}. Nothing is built.`
        : `A new image built from ${r.from_variant || 'main'}; ${r.project_slug} boots on it. Other projects are unchanged.`
    case 'skill':
      return r.status === 'fetched'
        ? 'Read SKILL.md under Inspect. Install puts in exactly that text and grants it to agents if its requirements are met.'
        : 'Nothing has been downloaded yet. Fetch makes the host download and check it, so you can read it before installing.'
    case 'journal':
      return 'An agent wrote it after reading untrusted content, so it stays out of every prompt until you approve it. Drop removes the line.'
    case 'page': return 'Anyone with the link can open it, and scripts in it run.'
    case 'site':
      return r.refused ? 'It can only be denied.'
        : "Allow always adds it to the always-allow list of the project's profile, so every "
          + 'project on that profile can reach it. Allow 1 h lets it through for that project '
          + 'for an hour and writes no list.'
    case 'ask': return 'The agent is waiting for your answer in the chat.'
    case 'alert': return countsLine(r.counts)
    default: return ''
  }
}

// The detail panel's title: "Commit #12", "Site", "Question in chat 4"
export function detailTitle(row) {
  if (row.kind === 'git') return `${row.label} #${row.id}`
  if (row.kind === 'ask') return row.chat ? `${row.label} in chat ${row.chat}` : row.label
  if (row.kind === 'alert') return row.subject || row.label
  return row.label[0].toUpperCase() + row.label.slice(1)
}

// The request kinds GET /api/security/request_step knows (it finds the chat
// step that filed one)
export const STEP_KINDS = ['git', 'package', 'environment', 'skill', 'service', 'page']

// ---- at a glance: the boxes that are running -----------------------------------

const BOX_NAME = { shared: 'Shared box', builder: 'Image builder' }

export function boxName(b) {
  if (BOX_NAME[b?.kind]) return BOX_NAME[b.kind]
  if (b?.kind === 'project') return `${b.project || b.id} box`
  if (b?.kind === 'service') return `Service box${b.project ? ` for ${b.project}` : ''}`
  return b?.id || 'Box'
}

export const imageLabel = (b) => {
  const v = b?.image?.variant || 'main'
  return b?.image?.version != null ? `${v} ${verLabel(b.image.version)}` : v
}

// {running: [{id, name, image, runtime, state, work: [{key, project, title,
// role, conversationId}]}], stopped: n}. `roles` maps a conversation id to its
// role in the agents tree ("@reviewer", "item 3"), when known.
export function glanceBoxes(boxes = [], roles = {}) {
  const running = []
  let stopped = 0
  for (const b of boxes || []) {
    if (b?.state !== 'running') { stopped += 1; continue }
    running.push({
      id: b.id, kind: b.kind, name: boxName(b), image: imageLabel(b), runtime: b.runtime || 'kvm',
      state: activityWord(b), project: b.project || null,
      work: doingNow(b).map((t) => ({ ...t, role: roles[t.conversationId] || '' })),
    })
  }
  return { running, stopped }
}

// conversation id -> role, from GET /api/chat/agents nodes
export function rolesOf(nodes = []) {
  const out = {}
  for (const n of nodes || []) if (n && n.id != null && n.role) out[n.id] = n.role
  return out
}
