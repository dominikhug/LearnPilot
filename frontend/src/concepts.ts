import type { Chunk, ConceptState, KeyIdeaStatus } from './api'

/** Mastery a concept needs, besides all key ideas last answered correctly. */
export const MASTERY_THRESHOLD = 0.7

export const stateIcon: Record<ConceptState, string> = {
  locked: '🔒',
  unlocked: '🔓',
  in_progress: '🟡',
  mastered: '✅',
}

export const stateLabel: Record<ConceptState, string> = {
  locked: 'Locked',
  unlocked: 'Unlocked',
  in_progress: 'In progress',
  mastered: 'Mastered',
}

export function chunkLocation(chunk: Chunk) {
  if (chunk.page !== null) return `Page ${chunk.page}`
  return chunk.section ?? `Passage ${chunk.position + 1}`
}

export const keyIdeaIcon: Record<KeyIdeaStatus, string> = {
  untested: '○',
  correct: '✅',
  partial: '◐',
  missing: '❌',
  misconception: '⚠️',
}

export const keyIdeaLabel: Record<KeyIdeaStatus, string> = {
  untested: 'Not tested',
  correct: 'Correct',
  partial: 'Partly right',
  missing: 'Missing',
  misconception: 'Misconception',
}

/** 1.5 → "1.5", 2 → "2" */
export function formatPoints(points: number) {
  return Number.isInteger(points) ? String(points) : points.toFixed(1)
}
