// node frontend/src/pages/__tests__/projectHome.test.mjs
import assert from 'node:assert/strict'
import {
  cadenceLabel, chatTitle, estimateTokens, findProject, firstLine, fmtTokens, parseStamp,
  relativeDay, summaryOf,
} from '../projectHome.js'

// ---- stamps: SQLite's bare UTC text, ISO with a zone, junk ----
assert.equal(parseStamp('2026-10-30 09:00:00'), Date.UTC(2026, 9, 30, 9, 0, 0))
assert.equal(parseStamp('2026-10-30T09:00:00+02:00'), Date.UTC(2026, 9, 30, 7, 0, 0))
assert.equal(parseStamp(''), null)
assert.equal(parseStamp('not a date'), null)

// ---- relative dates, by the reader's calendar (local time) ----
{
  const now = new Date(2026, 10, 13, 18, 0, 0)          // Nov 13, 18:00 local
  const at = (y, mo, d, h, mi = 0, s = 0) => new Date(y, mo, d, h, mi, s).toISOString()
  assert.equal(relativeDay(at(2026, 10, 13, 17, 59, 40), now), 'Just now')
  assert.equal(relativeDay(new Date(now.getTime() - 20 * 1000).toISOString(), now), 'Just now')
  assert.equal(relativeDay(at(2026, 10, 13, 17, 59), now), '1 minute ago')
  assert.equal(relativeDay(at(2026, 10, 13, 17, 15), now), '45 minutes ago')
  assert.equal(relativeDay(at(2026, 10, 13, 9, 0), now), '9 hours ago')
  assert.equal(relativeDay(at(2026, 10, 13, 17, 0), now), '1 hour ago')
  assert.equal(relativeDay(at(2026, 10, 12, 23, 0), now), 'Yesterday')
  assert.equal(relativeDay(at(2026, 10, 12, 1, 0), now), 'Yesterday')
  assert.equal(relativeDay(at(2026, 9, 30, 12, 0), now), 'Oct 30')
  assert.equal(relativeDay(at(2025, 9, 30, 12, 0), now), 'Oct 30, 2025')
  // the last hour reads in minutes even across midnight
  const early = new Date(2026, 10, 13, 0, 10)
  assert.equal(relativeDay(at(2026, 10, 12, 23, 50), early), '20 minutes ago')
  assert.equal(relativeDay(at(2026, 10, 12, 22, 0), early), 'Yesterday')
  // a clock a little ahead of ours is not "in the future"
  assert.equal(relativeDay(new Date(now.getTime() + 5000).toISOString(), now), 'Just now')
  // Yesterday across a month edge
  assert.equal(relativeDay(at(2026, 9, 31, 12, 0), new Date(2026, 10, 1, 9, 0)), 'Yesterday')
  assert.equal(relativeDay(null, now), '')
}

// ---- tokens ----
assert.equal(fmtTokens(950), '950')
assert.equal(fmtTokens(1234), '1.2k')
assert.equal(fmtTokens(12000), '12k')
assert.equal(fmtTokens(undefined), '0')
assert.equal(estimateTokens('abcdefgh'), 2)
assert.equal(estimateTokens(''), 0)

// ---- chat titles: the sidebar's rule ----
assert.equal(chatTitle({ id: 3, summary: '# Fix the build' }), 'Fix the build')
assert.equal(chatTitle({ id: 4, summary: '', started_at: '2026-10-30 09:15:00' }), '#4, 10-30 09:15')

// ---- the project's summary line out of project.md ----
const md = (summary) => `# Demo\n\n## Summary\n${summary}\n\n## Status\nJust created.\n\n## Journal\n- x\n`
assert.equal(summaryOf(md('A dashboard for the\nharbor.')), 'A dashboard for the harbor.')
assert.equal(summaryOf(md("(describe what you're building here)")), '')
assert.equal(summaryOf('# Demo\n\nFree text first.\n\n## Status\nx'), 'Free text first.')
assert.equal(summaryOf(''), '')
assert.equal(summaryOf('# Demo\n\n## Summary\n\n## Status\nJust created.'), '')

assert.equal(firstLine('\n  Answer in French.\nAlways.'), 'Answer in French.')
assert.equal(firstLine('x'.repeat(10), 5), 'xxxx…')

// ---- cadence ----
assert.equal(cadenceLabel({ cadence_kind: 'daily', daily_at: '07:30' }), 'Daily at 07:30')
assert.equal(cadenceLabel({ cadence_kind: 'interval', interval_minutes: 360 }), 'Every 6 hours')
assert.equal(cadenceLabel({ cadence_kind: 'interval', interval_minutes: 60 }), 'Every hour')
assert.equal(cadenceLabel({ cadence_kind: 'interval', interval_minutes: 45 }), 'Every 45 minutes')

// ---- /project <name> ----
{
  const ps = [{ slug: 'harbor', name: 'Harbor dashboard' }, { slug: 'hello', name: 'Hello world' },
              { slug: 'cntrl-site', name: 'Site' }]
  assert.equal(findProject(ps, 'harbor').slug, 'harbor')
  assert.equal(findProject(ps, 'Harbor Dashboard').slug, 'harbor')
  assert.equal(findProject(ps, '  SITE ').slug, 'cntrl-site')
  assert.equal(findProject(ps, 'harb').slug, 'harbor')        // a unique prefix
  assert.equal(findProject(ps, 'h'), null)                    // two match: no guess
  assert.equal(findProject(ps, 'nope'), null)
  assert.equal(findProject(ps, ''), null)
  assert.equal(findProject(null, 'x'), null)
}

console.log('projectHome: ok')
