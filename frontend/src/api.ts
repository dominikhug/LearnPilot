export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T = void>(path: string, init: RequestInit = {}): Promise<T> {
  // FormData bodies need the browser to set the multipart Content-Type itself.
  const headers = new Headers(init.headers)
  if (typeof init.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(`/api${path}`, { ...init, headers })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = typeof body?.detail === 'string' ? body.detail : response.statusText
    throw new ApiError(response.status, detail)
  }
  return response.status === 204 ? (undefined as T) : response.json()
}

export function errorMessage(e: unknown): string {
  return e instanceof ApiError ? e.message : 'Could not reach the server.'
}

export interface User {
  id: number
  name: string
}

export const auth = {
  me: () => api<User>('/auth/me'),
  login: (password: string) =>
    api('/auth/login', { method: 'POST', body: JSON.stringify({ password }) }),
  logout: () => api('/auth/logout', { method: 'POST' }),
}

export type DocumentStatus = 'processing' | 'ready' | 'failed'
export type ProcessingStep = 'counting_tokens' | 'done'

export interface Document {
  id: number
  title: string
  status: DocumentStatus
  step: ProcessingStep
  token_count: number | null
  language: string | null
  error_message: string | null
  can_retry: boolean
  created_at: string
}

export interface Chunk {
  id: number
  position: number
  page: number | null
  section: string | null
  text: string
}

export interface Limits {
  max_upload_mb: number
  max_document_tokens: number
  file_types: string[]
}

export const documents = {
  list: () => api<Document[]>('/documents'),
  limits: () => api<Limits>('/documents/limits'),
  get: (id: number) => api<Document>(`/documents/${id}`),
  chunks: (id: number) => api<Chunk[]>(`/documents/${id}/chunks`),
  upload: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return api<Document>('/documents', { method: 'POST', body })
  },
  retry: (id: number) => api<Document>(`/documents/${id}/retry`, { method: 'POST' }),
  remove: (id: number) => api(`/documents/${id}`, { method: 'DELETE' }),
}

/** Where a document opens: its hub when ready, otherwise the processing screen. */
export function documentRoute(document: Document) {
  return document.status === 'ready'
    ? { name: 'document', params: { id: document.id } }
    : { name: 'processing', params: { id: document.id } }
}
