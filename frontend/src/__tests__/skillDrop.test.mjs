// node frontend/src/__tests__/skillDrop.test.mjs
import assert from 'node:assert/strict'
import { describeUpload, fromFileList, isZipName, keepPath } from '../skillDrop.js'

const it = (p) => ({ path: p })
assert.equal(describeUpload([]), '')
assert.equal(describeUpload([it('demo.zip')]), 'demo.zip')
assert.equal(describeUpload([it('demo/SKILL.md'), it('demo/a/b.md')]), 'demo (2 files)')
assert.equal(describeUpload([it('demo/SKILL.md')]), 'demo (1 file)')
assert.equal(describeUpload([it('SKILL.md'), it('a.md')]), '2 files')
assert.ok(isZipName('X.ZIP') && !isZipName('a.zip.txt'))
assert.ok(keepPath('demo/SKILL.md') && !keepPath('demo/.git/config') && !keepPath('.DS_Store'))
assert.deepEqual(fromFileList([{ name: 'a.md', webkitRelativePath: 'd/a.md' },
  { name: '.DS_Store', webkitRelativePath: 'd/.DS_Store' }, { name: 'z.zip' }])
  .map((x) => x.path), ['d/a.md', 'z.zip'])
console.log('skillDrop ok')
