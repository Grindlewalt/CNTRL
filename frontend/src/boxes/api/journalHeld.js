// Journal lines held as [unverified] (backend/journalhold.py): written by a turn
// that had read untrusted content, kept out of the prompt until the operator decides.
import { enc, get, post, qs } from '../http.js'

export async function listHeldJournal(project) {
  const r = await get(`/api/memory/held-journal${qs({ project })}`)
  return r.items || []
}
// approve removes the tag from that line; drop removes the line. 409 = the line changed.
export const approveHeldJournal = (h) =>
  post(`/api/memory/held-journal/${enc(h.project)}/${enc(h.id)}/approve`)
export const dropHeldJournal = (h) =>
  post(`/api/memory/held-journal/${enc(h.project)}/${enc(h.id)}/drop`)
