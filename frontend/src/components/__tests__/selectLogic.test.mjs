// node frontend/src/components/__tests__/selectLogic.test.mjs
import assert from 'node:assert/strict'
import {
  arrange, buildOptions, displayFor, filterOptions, firstEnabled, MAX_RECENT_KEYS,
  normalizeOption, pickByNumber, pushRecent, readRecents, RECENT_STORE, recentOptions,
  SEARCH_AFTER, step, textOf, typeahead, writeRecent,
} from '../selectLogic.js'

// ---- options: strings, {value,label}, numbers, the placeholder idiom ----
assert.deepEqual(normalizeOption('apt'),
  { value: 'apt', label: 'apt', sub: null, disabled: false, group: null })
assert.equal(normalizeOption({ value: 7, label: '7 days' }).value, '7')   // native: always a string
assert.equal(normalizeOption({ value: 'x' }).label, 'x')
assert.equal(normalizeOption(null), null)
{
  const { list, placeholder } = buildOptions(['a', { value: 'b', label: 'Bee' }], [], 'Pick one')
  assert.deepEqual(list.map((o) => o.value), ['a', 'b'])
  assert.equal(placeholder, 'Pick one')
}
{
  // <option value="" disabled>Merge into project</option> written as a child
  const { list, placeholder } = buildOptions([], [{ value: '', label: 'Merge into project', disabled: true }, 'p1'])
  assert.equal(placeholder, 'Merge into project')
  assert.deepEqual(list.map((o) => o.value), ['p1'])
}
{
  // an enabled empty option is a real choice ("No project", "inherit (default)")
  const { list, placeholder } = buildOptions([{ value: '', label: 'No project' }, 'p1'])
  assert.equal(placeholder, null)
  assert.equal(list[0].label, 'No project')
}
assert.equal(textOf(['a', 1, { props: { children: ['b', null] } }]), 'a1b')

// ---- the trigger shows what a native select would ----
{
  const { list } = buildOptions(['read', 'write'])
  assert.equal(displayFor(list, 'write').option.value, 'write')
  assert.equal(displayFor(list, '', 'no access').placeholder, true)
  // a value that matches nothing shows the first enabled option, as React does
  assert.equal(displayFor(list, 'admin').option.value, 'read')
  assert.equal(displayFor([], '', null).option, null)
  // numbers compare as strings (Logs' keep_days)
  const days = buildOptions([{ value: 7, label: '7 days' }, { value: 30, label: '30 days' }]).list
  assert.equal(displayFor(days, 30).option.label, '30 days')
}

// ---- search ----
{
  const { list } = buildOptions([
    { value: 'gpt-5', label: 'GPT-5', group: 'OpenAI' },
    { value: 'deepseek-flash', label: 'DeepSeek Flash', group: 'DeepSeek' },
    { value: 'kimi', label: 'Kimi K3', sub: 'moonshot' },
    { value: 'flash-lite', label: 'Gemini Flash Lite' },
    { value: 'cafe', label: 'Café menu' },
  ])
  const v = (q) => filterOptions(list, q).map((o) => o.value)
  assert.deepEqual(v(''), list.map((o) => o.value))
  assert.deepEqual(v('flash'), ['deepseek-flash', 'flash-lite'])   // word-start, list order
  assert.deepEqual(v('deep'), ['deepseek-flash'])                   // label prefix first
  assert.deepEqual(v('moonshot'), ['kimi'])                         // sub line
  assert.deepEqual(v('openai'), ['gpt-5'])                          // group name
  assert.deepEqual(v('gemini lite'), ['flash-lite'])                // every term
  assert.deepEqual(v('cafe'), ['cafe'])                             // accents fold
  assert.deepEqual(v('FLASH'), ['deepseek-flash', 'flash-lite'])    // case folds
  assert.deepEqual(v('zzz'), [])
  // prefix of the label beats an earlier word-start match
  const two = buildOptions([{ value: 'a', label: 'Big flash' }, { value: 'b', label: 'Flash' }]).list
  assert.deepEqual(filterOptions(two, 'fla').map((o) => o.value), ['b', 'a'])
}

// ---- recents: order, dedupe, cap ----
assert.deepEqual(pushRecent([], 'a'), ['a'])
assert.deepEqual(pushRecent(['a', 'b'], 'b'), ['b', 'a'])
assert.deepEqual(pushRecent(['1', '2', '3', '4', '5', '6', '7', '8', '9'], '10'),
  ['10', '1', '2', '3', '4', '5', '6', '7', '8'])
assert.deepEqual(pushRecent(['7'], 7), ['7'])
{
  const { list } = buildOptions(['a', 'b', { value: 'c', disabled: true }, 'd'])
  // gone or disabled choices drop out; order is recency
  assert.deepEqual(recentOptions(list, ['d', 'gone', 'c', 'a']).map((o) => o.value), ['d', 'a'])
}

// ---- arrange: what the open list shows ----
const many = buildOptions('abcdefghij'.split('')).list            // 10 > SEARCH_AFTER
assert.ok(many.length > SEARCH_AFTER)
{
  // long list, no recents: one section, no numbers
  const { sections, items } = arrange(many, [])
  assert.equal(sections.length, 1)
  assert.equal(sections[0].label, null)
  assert.ok(items.every((it) => it.num === null))
}
{
  // long list with recents: Recent first, numbered by recency, not repeated below
  const { sections, items } = arrange(many, ['e', 'b', 'zz'])
  assert.equal(sections[0].label, 'Recent')
  assert.deepEqual(sections[0].items.map((it) => [it.value, it.num]), [['e', 1], ['b', 2]])
  assert.deepEqual(sections[1].items.map((it) => it.value), ['a', 'c', 'd', 'f', 'g', 'h', 'i', 'j'])
  assert.equal(items.length, many.length)
  assert.equal(pickByNumber(items, '1').value, 'e')
  assert.equal(pickByNumber(items, '2').value, 'b')
  assert.equal(pickByNumber(items, '3'), null)
  assert.equal(pickByNumber(items, '0'), null)
  assert.equal(pickByNumber(items, 'x'), null)
}
{
  // nine recents at most, numbered 1 to 9
  const { items } = arrange(many, 'jihgfedcba'.split(''))
  assert.deepEqual(items.filter((it) => it.num).map((it) => it.num), [1, 2, 3, 4, 5, 6, 7, 8, 9])
  assert.equal(pickByNumber(items, '9').value, 'b')
}
{
  // short list: the call site's order, numbered by position, recents ignored
  const short = buildOptions(['ask', 'allow', 'off']).list
  const { sections, items } = arrange(short, ['off'])
  assert.equal(sections.length, 1)
  assert.deepEqual(items.map((it) => [it.value, it.num]), [['ask', 1], ['allow', 2], ['off', 3]])
  assert.equal(pickByNumber(items, '3').value, 'off')
}
{
  // searching: filtered, no numbers, so a digit types instead of picking
  const { sections, items } = arrange(many, ['e'], 'e')
  assert.equal(sections.length, 1)
  assert.deepEqual(items.map((it) => it.value), ['e'])
  assert.equal(pickByNumber(items, '1'), null)
}
{
  // groups (an <optgroup>) become labelled sections, in order
  const grouped = buildOptions([
    { value: '', label: 'inherit' },
    { value: 'a1', group: 'A' }, { value: 'a2', group: 'A' },
    { value: 'b1', group: 'B' },
  ]).list
  const { sections } = arrange(grouped, [])
  assert.deepEqual(sections.map((s) => [s.label, s.items.length]), [[null, 1], ['A', 2], ['B', 1]])
}
{
  // a disabled short-list item keeps its number but cannot be picked by it
  const { items } = arrange(buildOptions(['a', { value: 'b', disabled: true }]).list, [])
  assert.equal(pickByNumber(items, '2'), null)
}

// ---- keyboard movement ----
{
  const items = [{ disabled: false }, { disabled: true }, { disabled: false }, { disabled: false }]
  assert.equal(step(items, 0, 1), 2)       // skips the disabled one
  assert.equal(step(items, 2, -1), 0)
  assert.equal(step(items, 3, 1), 3)       // no wrap at the end
  assert.equal(step(items, 0, -1), 0)      // nor at the start
  assert.equal(step(items, -1, 1), 0)      // nothing active: down starts at the top
  assert.equal(step(items, -1, -1), 3)     // up starts at the bottom
  assert.equal(step(items, 0, 8), 3)       // page down stops at the last
  assert.equal(step([], 0, 1), -1)
  assert.equal(firstEnabled([{ disabled: true }, { disabled: false }]), 1)
  assert.equal(firstEnabled([{ disabled: true }]), -1)
}
{
  const items = buildOptions(['apple', 'banana', 'avocado', { value: 'apricot', disabled: true }]).list
  assert.equal(typeahead(items, 'a'), 0)
  assert.equal(typeahead(items, 'a', 0), 2)  // the next one after the current
  assert.equal(typeahead(items, 'a', 2), 0)  // wraps, skipping the disabled apricot
  assert.equal(typeahead(items, 'b'), 1)
  assert.equal(typeahead(items, 'z'), -1)
}

// ---- storage: per key, survives junk, never throws ----
function memStore() {
  const m = new Map()
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), m }
}
{
  const s = memStore()
  assert.deepEqual(readRecents(s, 'Model'), [])
  writeRecent(s, 'Model', 'a')
  writeRecent(s, 'Model', 'b')
  writeRecent(s, 'Project', 'p')
  writeRecent(s, 'Model', 'a')
  assert.deepEqual(readRecents(s, 'Model'), ['a', 'b'])
  assert.deepEqual(readRecents(s, 'Project'), ['p'])
  assert.deepEqual(readRecents(s, ''), [])
  // the most recently used key is last; old keys fall off past the cap
  for (let i = 0; i < MAX_RECENT_KEYS + 5; i++) writeRecent(s, `k${i}`, 'v')
  const all = JSON.parse(s.getItem(RECENT_STORE))
  assert.equal(Object.keys(all).length, MAX_RECENT_KEYS)
  assert.equal(all.Model, undefined)
  assert.deepEqual(all[`k${MAX_RECENT_KEYS + 4}`], ['v'])
  // junk in storage reads as nothing and is replaced on the next write
  s.setItem(RECENT_STORE, '{not json')
  assert.deepEqual(readRecents(s, 'Model'), [])
  assert.deepEqual(writeRecent(s, 'Model', 'y'), ['y'])
  assert.deepEqual(readRecents(s, 'Model'), ['y'])
  s.setItem(RECENT_STORE, '[1,2]')
  assert.deepEqual(writeRecent(s, 'Model', 'x'), ['x'])
  assert.deepEqual(readRecents(s, 'Model'), ['x'])
}
{
  // storage that throws (private mode, quota) or is missing
  const bad = { getItem() { throw new Error('denied') }, setItem() { throw new Error('quota') } }
  assert.deepEqual(readRecents(bad, 'Model'), [])
  assert.deepEqual(writeRecent(bad, 'Model', 'a'), [])
  assert.deepEqual(readRecents(null, 'Model'), [])
  assert.deepEqual(writeRecent(null, 'Model', 'a'), [])
}

console.log('selectLogic: ok')
