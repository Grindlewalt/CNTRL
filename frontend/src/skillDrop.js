// Turning what a person drops or picks into a skill upload: a list of
// { path, file } where path is the relative path inside the folder (or the zip's
// own name). Pure helpers first, then the browser-only folder walk.

export const isZipName = (n) => /\.zip$/i.test(n || '')

// What the confirm step names: the zip, or the folder and its file count.
export function describeUpload(items) {
  if (!items.length) return ''
  if (items.length === 1 && isZipName(items[0].path)) return items[0].path
  const tops = new Set(items.map((i) => i.path.split('/')[0]))
  const n = items.length
  const files = `${n} file${n === 1 ? '' : 's'}`
  return tops.size === 1 && items.every((i) => i.path.includes('/'))
    ? `${[...tops][0]} (${files})` : files
}

// Skip what a folder drag drags along: OS litter and the .git folder.
export const keepPath = (p) => !/(^|\/)(\.DS_Store|\.git|__MACOSX)(\/|$)/.test(p)

// A picked set of files (an <input>): the folder picker gives webkitRelativePath.
export function fromFileList(list) {
  return [...list].map((f) => ({ path: f.webkitRelativePath || f.name, file: f }))
    .filter((i) => keepPath(i.path))
}

function readAll(reader) {
  return new Promise((res, rej) => {
    const out = []
    const next = () => reader.readEntries((batch) => {
      if (!batch.length) return res(out)
      out.push(...batch)
      next()
    }, rej)
    next()
  })
}

async function walk(entry, prefix, out) {
  if (!keepPath(prefix + entry.name)) return
  if (entry.isFile) {
    const file = await new Promise((res, rej) => entry.file(res, rej))
    out.push({ path: prefix + entry.name, file })
  } else if (entry.isDirectory) {
    for (const e of await readAll(entry.createReader())) {
      await walk(e, `${prefix}${entry.name}/`, out)
    }
  }
}

// A drop event's DataTransfer, keeping relative paths for dropped folders.
export async function fromDrop(dt) {
  const out = []
  const entries = [...dt.items].map((i) => i.webkitGetAsEntry?.()).filter(Boolean)
  if (!entries.length) return fromFileList(dt.files)
  for (const e of entries) await walk(e, '', out)
  return out
}
