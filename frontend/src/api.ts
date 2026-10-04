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
export type ProcessingStep = 'counting_tokens' | 'extracting_concepts' | 'building_graph' | 'done'

export interface Document {
  id: number
  title: string
  status: DocumentStatus
  step: ProcessingStep
  token_count: number | null
  language: string | null
  error_message: string | null
  can_retry: boolean
  concept_count: number
  created_at: string
}

export interface Chunk {
  id: number
  position: number
  page: number | null
  section: string | null
  text: string
}

export type ConceptState = 'locked' | 'unlocked' | 'in_progress' | 'mastered'
export type KeyIdeaStatus = 'untested' | 'correct' | 'partial' | 'missing' | 'misconception'

export interface TestedKeyIdea {
  id: number
  text: string
  status: KeyIdeaStatus
}

export interface Concept {
  id: number
  name: string
  definition: string
  level: number
  state: ConceptState
  mastery: number
  prerequisite_ids: number[]
  source_chunk_ids: number[]
  key_idea_count: number
  tested_key_ideas: TestedKeyIdea[]
}

export interface ConceptEdge {
  from_concept_id: number
  to_concept_id: number
  confidence: number
}

export interface ConceptGraph {
  concepts: Concept[]
  edges: ConceptEdge[]
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
  graph: (id: number) => api<ConceptGraph>(`/documents/${id}/graph`),
  upload: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return api<Document>('/documents', { method: 'POST', body })
  },
  retry: (id: number) => api<Document>(`/documents/${id}/retry`, { method: 'POST' }),
  remove: (id: number) => api(`/documents/${id}`, { method: 'DELETE' }),
}

export interface Question {
  id: number
  number: number
  text: string
  level: 'explain' | 'apply'
  points: number
}

export interface KeyIdeaFeedback extends TestedKeyIdea {
  feedback: string
}

export interface Answer {
  id: number
  question_id: number
  text: string
  graded: boolean
  points_possible: number
  points_earned: number | null
  score: number | null
  key_ideas: KeyIdeaFeedback[]
  source_chunk_ids: number[]
  dispute_reason: string | null
  regraded: boolean
  can_dispute: boolean
}

export interface LearningSession {
  concept_id: number
  concept_name: string
  document_id: number
  mastery: number
  mastered: boolean
  key_idea_count: number
  key_ideas_correct: number
  question: Question | null
  ungraded_answer: Answer | null
}

export interface Graded {
  answer: Answer
  mastery_before: number
  session: LearningSession
}

export const learning = {
  start: (conceptId: number) =>
    api<LearningSession>(`/concepts/${conceptId}/session`, { method: 'POST' }),
  answer: (questionId: number, text: string) =>
    api<Graded>(`/questions/${questionId}/answer`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  retryGrading: (answerId: number) => api<Graded>(`/answers/${answerId}/grade`, { method: 'POST' }),
  dispute: (answerId: number, reason: string) =>
    api<Graded>(`/answers/${answerId}/dispute`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
}

/** Where a document opens: its hub when ready, otherwise the processing screen. */
export function documentRoute(document: Document) {
  return document.status === 'ready'
    ? { name: 'document', params: { id: document.id } }
    : { name: 'processing', params: { id: document.id } }
}
