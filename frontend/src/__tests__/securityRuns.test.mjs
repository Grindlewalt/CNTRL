// node frontend/src/__tests__/securityRuns.test.mjs
import assert from 'node:assert/strict'
import {
  UNTRUSTED, cardTitle, cardTotals, cardsFor, chatHref, eventState, clock, countsLine, countsParts, eventActions,
  kindsToMute, lineTitle, muteAsk, mutable, programOf, runState, stopRunAsk, subjectsText,
} from '../securityRuns.js'
import {
  STEP_KINDS, alertRow, approveLabel, approveOpensInspect, boxName, changeText, denyLabel, detailTitle,
  glanceBoxes, imageLabel, queueRows, rolesOf, rowFor, summaryOf,
} from '../securityDash.js'

// the header's counts, as the sketch words them
assert.equal(countsLine({ need: 2, filtered: 47, reports: 1 }),
  '2 need you, 47 filtered as normal work, 1 agent report')
assert.equal(countsLine({ need: 1, filtered: 0, reports: 3 }), '1 need you, 3 agent reports')
assert.equal(countsLine({ need: 0, filtered: 0, reports: 0 }), '')
assert.equal(countsLine(), '')
// only the filtered count is a "(show)"
assert.deepEqual(countsParts({ need: 2, filtered: 5, reports: 1 }).map((p) => !!p.show),
  [false, true, false])

assert.equal(runState({ running: true }), 'running')
assert.equal(runState({ running: false }), 'finished')
assert.equal(runState({ running: null }), '')
assert.equal(runState({}), '')

// open chat at the step, or just the chat
assert.equal(chatHref(500, 12123), '/c/500?step=12123')
assert.equal(chatHref(500), '/c/500')
assert.equal(chatHref(null, 5), null)

assert.equal(UNTRUSTED, "the agent's words: untrusted")

const proc = {
  id: 9, kind: 'unexpected_process', severity: 'warn', tier: 'alert',
  detail: { exe: '/usr/bin/node', unit: 'jarvis-guest.service', pid: 812, box_id: 'p-bg' },
  doing: { conversation: { id: 500 }, step: { id: 12123, tool: 'run_code' } },
}
assert.deepEqual(programOf(proc), { exe: '/usr/bin/node', name: 'node', unit: 'jarvis-guest.service' })
assert.equal(programOf({ detail: {} }), null)
assert.equal(lineTitle(proc), 'New program outside the box baseline')
assert.equal(lineTitle({ kind: 'write_flag', summary: 'write flag: x in a.py' }), 'write flag: x in a.py')

// the process alert: allow, open chat at the step, stop, kill, mute, acknowledge
const ids = (ev, ctx) => eventActions(ev, ctx).map((a) => a.id)
assert.deepEqual(ids(proc, { running: true, hasChat: true }),
  ['allow', 'chat', 'stop', 'kill', 'mute', 'ack'])
assert.equal(eventActions(proc, { hasChat: true }).find((a) => a.id === 'chat').label,
  'Open chat at step')
// a finished run has nobody to stop; no chat means no chat link and no stop
assert.deepEqual(ids(proc, { running: false, hasChat: true }), ['allow', 'chat', 'kill', 'mute', 'ack'])
assert.deepEqual(ids(proc, { hasChat: false }), ['allow', 'kill', 'mute', 'ack'])
// no pid (an old alert): nothing to kill
assert.ok(!ids({ ...proc, detail: { exe: '/usr/bin/node' } }, { hasChat: true }).includes('kill'))

// a write flag: diff, revert, chat, stop, mute, acknowledge
const wf = { id: 3, kind: 'write_flag', severity: 'warn', tier: 'alert',
  detail: { path: 'tests/world.test.mjs', trigger: 'assertion_removed' } }
assert.deepEqual(ids(wf, { running: true, hasChat: true }),
  ['board', 'revert', 'chat', 'stop', 'mute', 'ack'])
assert.equal(eventActions(wf, { hasChat: true }).find((a) => a.id === 'chat').label, 'Open chat')
// a refused leak wrote nothing: no revert
assert.ok(!ids({ ...wf, detail: { path: 'a', refused: true } }, {}).includes('revert'))

// an anomaly cut: un-cut, the evidence; critical kinds are never mutable
const cut = { id: 4, kind: 'egress_anomaly', severity: 'critical', tier: 'critical',
  detail: { host: 'evil.example' } }
assert.deepEqual(ids(cut, { hasChat: true, running: true }), ['uncut', 'board', 'chat', 'stop', 'ack'])
assert.equal(mutable(cut), false)
assert.equal(mutable(wf), true)

// an agent report is "Mark resolved", and any other kind can be inspected
const fault = { id: 5, kind: 'harness_fault', severity: 'info', tier: 'record', detail: {} }
assert.equal(eventActions(fault, {}).slice(-1)[0].label, 'Mark resolved')
assert.ok(ids({ id: 6, kind: 'memory_proposed', severity: 'warn', tier: 'alert' }, {}).includes('board'))
// Acknowledge is always the last button
for (const ev of [proc, wf, cut, fault]) assert.equal(ids(ev, { hasChat: true }).slice(-1)[0], 'ack')

// "(node, crashpad)" only when there is more than one thing to name
assert.equal(subjectsText({ n: 2, subjects: ['node', 'crashpad'] }), '(node, crashpad)')
assert.equal(subjectsText({ n: 1, subjects: ['node'] }), '')
assert.equal(subjectsText({ n: 2, subjects: ['node'] }), '(node)')
assert.equal(subjectsText({ n: 1, subjects: [] }), '')

// muting a whole card leaves the critical kinds alone
const run = { kinds: [{ kind: 'unexpected_process', tier: 'alert', severity: 'warn' },
  { kind: 'write_flag', tier: 'alert', severity: 'warn' },
  { kind: 'egress_anomaly', tier: 'critical', severity: 'critical' }] }
assert.deepEqual(kindsToMute(run), ['unexpected_process', 'write_flag'])
assert.match(muteAsk(['a', 'b']).title, /2 kinds/)
assert.match(muteAsk(['a']).title, /Mute a\?/)

// stopping a whole run names how many agents, and the plan if one drives them
const q = stopRunAsk({ agents: [1, 2, 3], plans: ['proj'] }, 'chat 500')
assert.match(q.title, /whole run: chat 500/)
assert.match(q.body, /3 agents running and the plan of proj/)
assert.match(stopRunAsk({ agents: [1], plans: [] }).body, /^1 agent running would/)

// the project panel shows only its own cards; the totals match the badge
const runs = [{ key: 'a', project: 'p', counts: { need: 2, reports: 1 } },
  { key: 'b', project: 'q', counts: { need: 1, reports: 0 } },
  { key: 'c', project: null, counts: { need: 0, reports: 2 } }]
assert.deepEqual(cardsFor(runs, 'p').map((r) => r.key), ['a'])
assert.equal(cardsFor(runs, null).length, 3)
assert.deepEqual(cardTotals(runs), { need: 3, reports: 3, runs: 3 })
assert.deepEqual(cardTotals(undefined), { need: 0, reports: 0, runs: 0 })

assert.equal(clock('2026-10-01 05:31:12'), '05:31')
assert.equal(clock('2026-10-01T05:31:12Z'), '05:31')
assert.equal(clock(''), '')

// card titles: the project is in the meta line, a bare kind reads as words
assert.equal(cardTitle({ title: 'website · git_mirror_failed', project: 'website' }), 'Git mirror failed')
assert.equal(cardTitle({ title: 'login_failed', project: null }), 'Login failed')
assert.equal(cardTitle({ title: 'site · chat 4 "Fix the nav"', project: 'site' }), 'Chat 4 "Fix the nav"')
assert.equal(cardTitle({ title: 'box p-1 · boot 3' }), 'Box p-1, boot 3')
assert.equal(cardTitle({}), '')
assert.equal(cardTitle({ title: 'site · harness_fault', project: 'site' }), 'Agent report')

// an event's state word: yolo approvals and failed pushes say what they are
assert.equal(eventState({ kind: 'yolo_auto_approved', severity: 'info' }).word, 'Approved without asking')
assert.equal(eventState({ kind: 'git_mirror_failed', severity: 'warn', detail: { error: 'denied' } }).tone, 'bad')
assert.equal(eventState({ kind: 'git_mirror_failed', detail: { error: 'denied' } }).why, 'denied')
assert.deepEqual([eventState({ severity: 'critical' }).tone, eventState({ severity: 'warn' }).tone,
  eventState({ severity: 'info' }).tone], ['bad', 'warn', 'quiet'])
assert.equal(lineTitle({ kind: 'yolo_auto_approved', summary: 'approved unasked (yolo): package pip x' }),
  'package pip x')

// ---- the dashboard (securityDash.js) ------------------------------------------
{

// the agent's summary first; else the best short reason the row carries
assert.deepEqual(summaryOf('git', { message: 'Add loader\n\nlong body', agent_summary: '  Adds the loader.\n It reads CSV.' }),
  { summary: 'Adds the loader. It reads CSV.', byAgent: true })
assert.deepEqual(summaryOf('git', { message: '\nAdd loader\n\nbody' }), { summary: 'Add loader', byAgent: false })
assert.equal(summaryOf('git', { kind: 'remote', message: 'https://github.com/a/b' }).summary,
  "Connect https://github.com/a/b as this project's remote and push its commits.")
assert.equal(summaryOf('package', { reason: 'for video export' }).summary, 'for video export')
assert.equal(summaryOf('service', { description: 'tiny api', reason: 'r' }).summary, 'tiny api')
assert.equal(summaryOf('site', { host: 'x.com' }).summary,
  'A box tried to reach it and no list covers it, so it was blocked.')
assert.equal(summaryOf('site', { triage_reason: 'looks like a tracker' }).summary, 'looks like a tracker')
assert.equal(summaryOf('site', { refused: 'the CNTRL host' }).summary, 'It cannot be allowed: the CNTRL host.')
assert.equal(summaryOf('page', { name: 'Report' }).summary, 'Publish Report at a link anyone can open.')

// rows: kinds, labels, keys
const git = rowFor('git', { id: 3, slug: 'web', kind: 'push', message: 'Add x', created_at: '2026-11-13 10:00:00' })
assert.deepEqual([git.kind, git.sub, git.label, git.project, git.key], ['git', 'push', 'Pull request', 'web', 'git:web:3'])
assert.equal(rowFor('git', { id: 1, slug: 'w', message: 'm' }).label, 'Commit')
assert.equal(rowFor('git', { id: 1, slug: 'w', message: 'm', error: 'push failed' }).tone, 'bad')
assert.equal(rowFor('package', { id: 2, manager: 'pip', package: 'rich', resolved_version: '13.0' }).subject, 'rich 13.0')
assert.equal(rowFor('environment', { id: 2, kind: 'use', name: 'desktop' }).label, 'Image change')
assert.equal(rowFor('site', { id: 9, host: 'a.io', refused: 'no' }).tone, 'bad')
assert.equal(rowFor('ask', { id: 'k1', kind: 'permission', question: 'Run it?', conversation_id: 4 }).label, 'Permission')
assert.equal(rowFor('nope', {}), null)

// alerts: one row per run; info-only runs and agent reports fold away
const runs = [
  { key: 'r1', project: 'web', last_at: '2026-11-13 09:00:00', counts: { need: 2 },
    kinds: [{ kind: 'write_flag', severity: 'warn' }], events: [{ kind: 'write_flag', summary: 'odd write' }] },
  { key: 'r2', last_at: '2026-11-13 11:00:00', counts: { need: 1 },
    kinds: [{ kind: 'x', severity: 'info' }], events: [{ kind: 'x', summary: 'fyi' }] },
  { key: 'r3', counts: { reports: 1 }, kinds: [], report_kinds: [{ kind: 'harness_fault' }], events: [] },
]
assert.deepEqual([alertRow(runs[0]).tone, alertRow(runs[0]).label, alertRow(runs[0]).summary],
  ['warn', '2 alerts', 'odd write'])
assert.equal(alertRow({ key: 'c', counts: { need: 1 }, kinds: [{ severity: 'critical' }] }).tone, 'bad')
assert.equal(alertRow(runs[2]).label, 'Agent report')
const q = queueRows({
  git: [{ id: 1, slug: 'web', message: 'old', created_at: '2026-11-13 08:00:00' }],
  packages: [{ id: 5, manager: 'apt', package: 'ffmpeg', created_at: '2026-11-13 12:00:00' }],
  asks: [{ id: 'a', question: 'Which branch?', conversation_id: 2 }],
  runs,
})
// questions first, then requests newest first, then the alerts that need you
assert.deepEqual(q.rows.map((r) => r.key), ['ask:a', 'package:5', 'git:web:1', 'alert:r1'])
assert.deepEqual(q.quiet.map((r) => r.key), ['alert:r2', 'alert:r3'])
assert.deepEqual(queueRows({}), { rows: [], quiet: [] })

// buttons: refuse, approve, and which approvals need the full request first
assert.deepEqual([denyLabel(git), approveLabel(git)], ['Deny', 'Merge'])
assert.equal(approveLabel(rowFor('git', { id: 1, slug: 'w', message: 'm' })), 'Approve')
assert.equal(approveLabel(rowFor('git', { id: 1, slug: 'w', kind: 'remote', message: 'u' })), 'Connect')
assert.equal(approveLabel(rowFor('git', { id: 1, slug: 'w', message: 'm', error: 'x' })), 'Retry')
assert.equal(denyLabel(rowFor('journal', { id: 1, project: 'w', text: 't' })), 'Drop')
assert.equal(denyLabel(alertRow(runs[0])), null)
assert.equal(approveLabel(rowFor('skill', { id: 1, status: 'pending', source: 's' })), 'Fetch and read')
assert.equal(approveLabel(rowFor('skill', { id: 1, status: 'fetched', source: 's' })), 'Install and grant')
assert.equal(approveOpensInspect(rowFor('service', { id: 1, name: 'api' })), true)
assert.equal(approveOpensInspect(rowFor('environment', { id: 1, name: 'e', resolved: false })), true)
assert.equal(approveOpensInspect(rowFor('environment', { id: 1, name: 'e', resolved: true })), false)
assert.equal(approveOpensInspect(rowFor('environment', { id: 1, kind: 'use', name: 'e' })), false)
assert.equal(approveOpensInspect(git), false)
assert.ok(changeText(git).includes('into main'))
assert.equal(detailTitle(git), 'Pull request #3')
assert.equal(detailTitle(rowFor('ask', { id: 'a', question: 'q', conversation_id: 4 })), 'Question in chat 4')
assert.equal(detailTitle(rowFor('package', { id: 2, manager: 'pip', package: 'rich' })), 'Pip package')

// boxes at a glance: the running ones, their image, what works in them
const g = glanceBoxes([
  { id: 'shared', kind: 'shared', state: 'running', image: { variant: 'main', version: 7 },
    now: [{ op_id: 'o1', conversation_id: 12, project: 'web', title: 'Fix nav' }] },
  { id: 'p-site', kind: 'project', project: 'site', state: 'running', image: { variant: 'desktop', version: null }, now: [] },
  { id: 'p-x', kind: 'project', project: 'x', state: 'stopped', image: { variant: 'main' } },
], rolesOf([{ id: 12, role: '@reviewer' }, { id: 13 }]))
assert.equal(g.stopped, 1)
assert.deepEqual(g.running.map((b) => [b.name, b.image]), [['Shared box', 'main v7'], ['site box', 'desktop']])
assert.deepEqual(g.running[0].work.map((w) => [w.project, w.title, w.role]), [['web', 'Fix nav', '@reviewer']])
assert.equal(boxName({ kind: 'service', project: 'web' }), 'Service box for web')
assert.equal(imageLabel({}), 'main')
assert.ok(STEP_KINDS.includes('git') && !STEP_KINDS.includes('site'))
}

console.log('securityRuns ok')
