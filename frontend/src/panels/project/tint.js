// A syntax tint for the file viewer's plain view: comments, strings, numbers
// and a short list of keywords get a class each; everything else stays ink.
// It is a tint, not a highlighter: one pass, no grammar per language, so an
// odd file degrades to plain text rather than to a wrong parse. Pure, so node
// can test it (see __tests__/tint.test.mjs).

const KEYWORDS = new Set([
  'def', 'class', 'import', 'from', 'return', 'if', 'elif', 'else', 'for', 'while', 'in',
  'not', 'and', 'or', 'lambda', 'try', 'except', 'finally', 'with', 'as', 'yield', 'pass',
  'raise', 'async', 'await', 'None', 'True', 'False', 'self',
  'const', 'let', 'var', 'function', 'export', 'default', 'new', 'this', 'true', 'false',
  'null', 'undefined', 'switch', 'case', 'break', 'continue', 'typeof', 'instanceof',
  'throw', 'catch', 'extends', 'static', 'public', 'private', 'void', 'int', 'str', 'bool',
  'fn', 'struct', 'enum', 'impl', 'match', 'mut', 'pub', 'use', 'where', 'package', 'func',
  'type', 'interface', 'select', 'go', 'defer', 'chan', 'map', 'range',
])

// Which comment syntax a file uses, by extension. Hash-style is the default
// (py, sh, toml, yaml, rb, conf); slash-style for the C family and the web.
const SLASH = /\.(js|jsx|mjs|cjs|ts|tsx|c|cc|cpp|h|hpp|java|kt|swift|go|rs|cs|php|scss|css|less)$/i
const NO_COMMENTS = /\.(json|csv|tsv|txt|md|markdown|rst|log|html?|xml|svg)$/i

export function commentStyle(path) {
  if (NO_COMMENTS.test(path || '')) return 'none'
  if (SLASH.test(path || '')) return 'slash'
  return 'hash'
}

// Returns [{ t: 'c' | 's' | 'n' | 'k' | '', v: text }], concatenating to the input.
export function tint(text, path = '') {
  const style = commentStyle(path)
  const out = []
  let plain = ''
  const flush = () => { if (plain) { out.push({ t: '', v: plain }); plain = '' } }
  const push = (t, v) => { flush(); out.push({ t, v }) }
  const n = text.length
  let i = 0
  while (i < n) {
    const ch = text[i]
    const nx = text[i + 1]
    // comments
    if (style === 'hash' && ch === '#') {
      let j = text.indexOf('\n', i); if (j < 0) j = n
      push('c', text.slice(i, j)); i = j; continue
    }
    if (style === 'slash' && ch === '/' && nx === '/') {
      let j = text.indexOf('\n', i); if (j < 0) j = n
      push('c', text.slice(i, j)); i = j; continue
    }
    if (style === 'slash' && ch === '/' && nx === '*') {
      let j = text.indexOf('*/', i + 2); j = j < 0 ? n : j + 2
      push('c', text.slice(i, j)); i = j; continue
    }
    // strings: '…' "…" `…`, one line for quotes (an unclosed one ends at the
    // line), any length for backticks; a backslash skips the next char
    if (ch === '"' || ch === "'" || ch === '`') {
      let j = i + 1
      while (j < n) {
        if (text[j] === '\\') { j += 2; continue }
        if (text[j] === ch) { j += 1; break }
        if (text[j] === '\n' && ch !== '`') break
        j += 1
      }
      push('s', text.slice(i, Math.min(j, n))); i = Math.min(j, n); continue
    }
    // numbers (not inside a word)
    if (/[0-9]/.test(ch) && !/[A-Za-z0-9_]/.test(text[i - 1] || '')) {
      let j = i + 1
      while (j < n && /[0-9A-Fa-fxX._]/.test(text[j])) j += 1
      push('n', text.slice(i, j)); i = j; continue
    }
    // words
    if (/[A-Za-z_]/.test(ch)) {
      let j = i + 1
      while (j < n && /[A-Za-z0-9_]/.test(text[j])) j += 1
      const w = text.slice(i, j)
      if (KEYWORDS.has(w)) push('k', w); else plain += w
      i = j; continue
    }
    plain += ch
    i += 1
  }
  flush()
  return out
}
