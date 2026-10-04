"""Learner model: points, score and mastery, computed in code from graded answers."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from learnpilot.models import KeyIdeaStatus

# Weight of the newest score in the moving average.
ALPHA = 0.5
# Mastery a concept needs, besides all key ideas last answered correctly.
MASTERY_THRESHOLD = 0.7

POINTS = {
    KeyIdeaStatus.correct: 1.0,
    KeyIdeaStatus.partial: 0.5,
    KeyIdeaStatus.missing: 0.0,
    KeyIdeaStatus.misconception: 0.0,
}
FAILED = {KeyIdeaStatus.missing, KeyIdeaStatus.misconception}

# One graded answer: key idea id -> status, for the key ideas its question tested.
type Grades = Mapping[int, KeyIdeaStatus]


def points_earned(grades: Grades) -> float:
    return sum(POINTS[status] for status in grades.values())


def score(grades: Grades) -> float:
    """Points earned / points possible; each tested key idea is worth one point."""
    return points_earned(grades) / len(grades) if grades else 0.0


def next_mastery(mastery: float, answer_score: float) -> float:
    return ALPHA * answer_score + (1 - ALPHA) * mastery


@dataclass
class Progress:
    mastery: float = 0.0
    statuses: dict[int, KeyIdeaStatus] = field(default_factory=dict)
    failed_attempts: dict[int, int] = field(default_factory=dict)
    # Index of the answer after which the concept first counted as mastered.
    mastered_after: int | None = None

    def correct_count(self) -> int:
        return sum(s == KeyIdeaStatus.correct for s in self.statuses.values())


def replay(key_idea_ids: Iterable[int], answers: Iterable[Grades]) -> Progress:
    """Rebuilds a concept's progress from its graded answers, oldest first.

    Mastered is permanent: once reached, a later weak answer lowers mastery but
    does not undo it.
    """
    progress = Progress(statuses={i: KeyIdeaStatus.untested for i in key_idea_ids})
    progress.failed_attempts = dict.fromkeys(progress.statuses, 0)
    for index, grades in enumerate(answers):
        # Key ideas deleted since the answer was graded no longer count.
        grades = {i: s for i, s in grades.items() if i in progress.statuses}
        if not grades:
            continue
        progress.mastery = next_mastery(progress.mastery, score(grades))
        for key_idea_id, status in grades.items():
            progress.statuses[key_idea_id] = status
            progress.failed_attempts[key_idea_id] += status in FAILED
        if progress.mastered_after is None and is_mastered(progress):
            progress.mastered_after = index
    return progress


def is_mastered(progress: Progress) -> bool:
    return (
        all(s == KeyIdeaStatus.correct for s in progress.statuses.values())
        and progress.mastery > MASTERY_THRESHOLD
    )
