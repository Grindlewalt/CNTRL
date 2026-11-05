// Published static pages (backend/pages_api.py): an agent asks to publish a file or
// folder of a project, the operator approves it, and a copy is served at its own link.
import { enc, get, post } from '../http.js'

// live pages, newest first
export async function listPages() {
  const r = await get('/api/pages')
  return r.items || []
}
// requests waiting for the operator
export async function listPageRequests() {
  const r = await get('/api/pages/requests')
  return r.items || []
}
// approve answers { ok, url }
export const approvePage = (p) => post(`/api/pages/requests/${enc(p.id)}/approve`)
export const rejectPage = (p) => post(`/api/pages/requests/${enc(p.id)}/reject`)
export const unpublishPage = (p) => post(`/api/pages/${enc(p.id)}/unpublish`)
