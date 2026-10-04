import type { Chunk, ConceptState } from './api'

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
