import assert from 'node:assert/strict'
import { test } from 'node:test'
import { commentStyle, tint } from '../tint.js'

const join = (toks) => toks.map((t) => t.v).join('')

test('tint concatenates back to the input', () => {
  const src = 'def f(x):\n    return "a\\"b" + 42  # done\n'
  assert.equal(join(tint(src, 'a.py')), src)
})

test('python: hash comments, strings, numbers, keywords', () => {
  const toks = tint('x = 10 # ten\nprint("hi")\n', 'a.py')
  assert.deepEqual(toks.filter((t) => t.t === 'c').map((t) => t.v), ['# ten'])
  assert.deepEqual(toks.filter((t) => t.t === 's').map((t) => t.v), ['"hi"'])
  assert.deepEqual(toks.filter((t) => t.t === 'n').map((t) => t.v), ['10'])
  assert.equal(tint('import os\n', 'a.py')[0].t, 'k')
})

test('javascript: slash and block comments; a hash is not a comment', () => {
  const toks = tint('const a = 1 // one\n/* two */ #x\n', 'a.js')
  assert.deepEqual(toks.filter((t) => t.t === 'c').map((t) => t.v), ['// one', '/* two */'])
  assert.equal(toks.filter((t) => t.v.includes('#x')).every((t) => t.t === ''), true)
})

test('markdown and json get no comments; a url is not a comment', () => {
  assert.equal(commentStyle('README.md'), 'none')
  assert.equal(commentStyle('data.json'), 'none')
  const toks = tint('see https://x.y/#frag\n', 'README.md')
  assert.equal(toks.some((t) => t.t === 'c'), false)
})

test('an unclosed quote ends at the line, not the file', () => {
  const toks = tint("a = 'oops\nb = 2\n", 'a.py')
  const s = toks.find((t) => t.t === 's')
  assert.equal(s.v, "'oops")
  assert.equal(toks.some((t) => t.t === 'n' && t.v === '2'), true)
})

test('numbers inside words stay plain', () => {
  const toks = tint('v2 = 3\n', 'a.py')
  assert.deepEqual(toks.filter((t) => t.t === 'n').map((t) => t.v), ['3'])
})
