// node frontend/src/__tests__/chatAnchor.test.mjs
import assert from 'node:assert/strict'
import { anchorValue, needles, readAnchor } from '../chatAnchor.js'

// an anchor reads back only for its own chat
const raw = anchorValue(12, 345, 'deploy key')
assert.deepEqual(readAnchor(raw, 12), { cid: 12, mid: 345, q: 'deploy key' })
assert.deepEqual(readAnchor(raw, '12'), { cid: 12, mid: 345, q: 'deploy key' })
assert.equal(readAnchor(raw, 13), null)
assert.equal(readAnchor('', 12), null)
assert.equal(readAnchor('12:345', 12), null)            // junk, not JSON
assert.equal(readAnchor(anchorValue(12, 0), 12), null)  // no message id
assert.equal(readAnchor(anchorValue(12, 'x'), 12), null)
assert.equal(JSON.parse(anchorValue(1, 2, 'q'.repeat(500))).q.length, 200)

// what to look for: the whole query, then its longest word
assert.deepEqual(needles('Deploy Key rotation'), ['deploy key rotation', 'rotation'])
assert.deepEqual(needles('  harbor '), ['harbor'])
assert.deepEqual(needles(''), [])

console.log('chatAnchor: ok')
