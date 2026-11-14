// The one rule for a project's name that the UI can check before the server
// does: it can't start with a number (the backend refuses it too, in create,
// import and rename). Returns the problem as a sentence, or '' when fine.
export const NAME_RULE = "A project name can't start with a number."

export function nameProblem(name) {
  return /^\d/.test((name || '').trim()) ? NAME_RULE : ''
}
