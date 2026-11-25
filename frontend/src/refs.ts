// Per-project ids of tasks and docs (backend: services/refs.py): MER-12 is task 12, MER-D3 is doc 3.

export function taskRef(key: string, task: { number: number }): string {
  return `${key}-${task.number}`;
}

export function docRef(key: string, doc: { number: number }): string {
  return `${key}-D${doc.number}`;
}

/** Same rule as the backend: 2-4 letters or digits, starting with a letter. */
export const PROJECT_KEY_RE = /^[A-Z][A-Z0-9]{1,3}$/;
