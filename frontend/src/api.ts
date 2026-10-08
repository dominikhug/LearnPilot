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
  mastered_count: number
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
  /** Locked concepts only: the missing prerequisite to learn first. */
  learn_first_id: number | null
}

export interface ConceptEdge {
  from_concept_id: number
  to_concept_id: number
  confidence: number
}

export interface ConceptGraph {
  concepts: Concept[]
  edges: ConceptEdge[]
  /** The automatic choice for "Start learning"; null when everything is mastered. */
  next_concept_id: number | null
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

/** Graph editing lists every key idea, untested ones included. */
export type KeyIdea = TestedKeyIdea

export const editing = {
  rename: (conceptId: number, name: string) =>
    api(`/concepts/${conceptId}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  removeConcept: (conceptId: number) => api(`/concepts/${conceptId}`, { method: 'DELETE' }),
  addPrerequisite: (conceptId: number, prerequisiteId: number) =>
    api<ConceptEdge>(`/concepts/${conceptId}/prerequisites`, {
      method: 'POST',
      body: JSON.stringify({ prerequisite_id: prerequisiteId }),
    }),
  removePrerequisite: (conceptId: number, prerequisiteId: number) =>
    api(`/concepts/${conceptId}/prerequisites/${prerequisiteId}`, { method: 'DELETE' }),
  keyIdeas: (conceptId: number) => api<KeyIdea[]>(`/concepts/${conceptId}/key-ideas`),
  /** A changed text resets the status; the key idea comes back with a new id. */
  editKeyIdea: (keyIdeaId: number, text: string) =>
    api<KeyIdea>(`/key-ideas/${keyIdeaId}`, { method: 'PATCH', body: JSON.stringify({ text }) }),
  removeKeyIdea: (keyIdeaId: number) => api(`/key-ideas/${keyIdeaId}`, { method: 'DELETE' }),
}

export interface Question {
  id: number
  number: number
  text: string
  level: 'explain' | 'apply'
  origin: 'plan' | 'follow_up'
  points: number
}

export interface KeyIdeaFeedback extends TestedKeyIdea {
  feedback: string
}

export interface KeyIdeaRef {
  id: number
  text: string
}

export interface Explanation {
  id: number
  answer_id: number
  key_ideas: KeyIdeaRef[]
  angle: 'analogy' | 'example' | 'step_by_step'
  text: string
  source_chunk_ids: number[]
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
  needs_explanation: boolean
  explanation: Explanation | null
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

export interface ConceptRef {
  id: number
  name: string
  state: ConceptState
  mastery: number
}

export interface Completion {
  mastery: number
  newly_unlocked: ConceptRef[]
  next_concept: ConceptRef | null
  document_completed: boolean
}

export interface WayOut {
  key_ideas: KeyIdeaRef[]
  prerequisite: ConceptRef | null
  other_concept: ConceptRef | null
}

export interface Graded {
  answer: Answer
  mastery_before: number
  session: LearningSession
  completed: Completion | null
  way_out: WayOut | null
}

export const learning = {
  /** Starts or resumes a concept; a locked one needs startLocked. */
  start: (conceptId: number, startLocked = false) =>
    api<LearningSession>(
      `/concepts/${conceptId}/session${startLocked ? '?start_locked=true' : ''}`,
      { method: 'POST' },
    ),
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
  explain: (answerId: number) =>
    api<Explanation>(`/answers/${answerId}/explanation`, { method: 'POST' }),
}

export type LlmPurpose =
  | 'concept_extraction'
  | 'question_plan'
  | 'grading'
  | 'follow_up_question'
  | 'explanation'

export interface Tokens {
  calls: number
  input_tokens: number
  output_tokens: number
}

export interface Usage {
  /** All users' tokens today; the daily limit counts these. */
  used_today: number
  daily_limit: number
  resets_at: string
  paused: boolean
  /** The current user's calls, all time. */
  total: Tokens
  /** document_id null: documents deleted since. */
  by_document: (Tokens & { document_id: number | null; title: string | null })[]
  by_purpose: (Tokens & { purpose: LlmPurpose })[]
}

export const usage = {
  get: () => api<Usage>('/usage'),
}

/** Where a document opens: its hub when ready, otherwise the processing screen. */
export function documentRoute(document: Document) {
  return document.status === 'ready'
    ? { name: 'document', params: { id: document.id } }
    : { name: 'processing', params: { id: document.id } }
}
