// The Logs transcript's timeline, as the API sends it (/api/logs/conversations/{id}).
//
// Three kinds of item, in the order things happened: `message` (the operator's
// or the assistant's), `tool` (a call with its args and result), and
// `narration` (the text the agent wrote between its calls: its own words, not a
// reply and not a tool result). Only messages and narration are prose, and only
// narration is labelled as the agent thinking aloud.

// The one line above the timeline, as a sentence: how many items, and how many
// of them are the agent's between-calls text (left out when a chat from before
// that was kept has none)
export function timelineHeader(items = []) {
  const notes = items.filter((i) => i.kind === 'narration').length
  const n = `${items.length} item${items.length === 1 ? '' : 's'} in order`
  return notes ? `${n}, ${notes} of them the agent's own notes.` : `${n}.`
}

// The role label of a prose item: what the reader sees as its head
export function proseLabel(item) {
  if (item.kind === 'narration') return "Agent's own text"
  if (item.role === 'user') return 'You'
  if (item.role === 'assistant') return 'Assistant'
  return item.role ? item.role[0].toUpperCase() + item.role.slice(1) : ''
}
