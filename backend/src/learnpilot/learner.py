"""Learner model: points, score and mastery, computed in code from graded answers."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from learnpilot.models import KeyIdeaStatus

# Weight of the newest score in the moving average.
ALPHA = 0.5
# Mastery a concept needs, besides all key ideas last answered correctly.
MASTERY_THRESHOLD = 0.7
# Failed attempts on one key idea after which the learner is offered a way out.
ATTEMPT_LIMIT = 3
# Key ideas one check question tests at most, as in the question plan.
MAX_KEY_IDEAS_PER_QUESTION = 3

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
    # Failed attempts since the key idea was last answered correctly.
    failed_attempts: dict[int, int] = field(default_factory=dict)
    # Index of the answer that last tested each key idea.
    last_tested: dict[int, int] = field(default_factory=dict)
    # Points per answer that tested each key idea, oldest first.
    points: dict[int, list[float]] = field(default_factory=dict)
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
    progress.points = {i: [] for i in progress.statuses}
    for index, grades in enumerate(answers):
        # Key ideas deleted since the answer was graded no longer count.
        grades = {i: s for i, s in grades.items() if i in progress.statuses}
        if not grades:
            continue
        progress.mastery = next_mastery(progress.mastery, score(grades))
        for key_idea_id, status in grades.items():
            progress.statuses[key_idea_id] = status
            progress.last_tested[key_idea_id] = index
            progress.points[key_idea_id].append(POINTS[status])
            if status == KeyIdeaStatus.correct:
                progress.failed_attempts[key_idea_id] = 0
            else:
                progress.failed_attempts[key_idea_id] += status in FAILED
        if progress.mastered_after is None and is_mastered(progress):
            progress.mastered_after = index
    return progress


def is_mastered(progress: Progress) -> bool:
    return (
        all(s == KeyIdeaStatus.correct for s in progress.statuses.values())
        and progress.mastery > MASTERY_THRESHOLD
    )


def reached_attempt_limit(progress: Progress, key_idea_id: int) -> bool:
    """True after every third failed attempt, so the way out is offered again later."""
    attempts = progress.failed_attempts[key_idea_id]
    return attempts > 0 and attempts % ATTEMPT_LIMIT == 0


@dataclass
class NextQuestion:
    """Either a queued question or the key ideas a new follow-up question should test."""

    question_id: int | None = None
    key_idea_ids: list[int] = field(default_factory=list)


def next_question(
    key_idea_ids: list[int], answers: list[Grades], queue: list[tuple[int, list[int]]]
) -> NextQuestion:
    """Picks what to ask after the graded `answers` (oldest first).

    `queue` holds the unasked questions as (id, tested key idea ids), in plan order.
    A key idea last answered `missing` or `misconception` was re-explained and
    needs a check question; if another key idea is still open, a question on that
    one comes first, so the check tests recall rather than what was just read.
    """
    progress = replay(key_idea_ids, answers)
    statuses = progress.statuses
    failed = [i for i in key_idea_ids if statuses[i] in FAILED]
    latest = len(answers) - 1

    def queued(accept) -> NextQuestion | None:
        for question_id, tested in queue:
            if accept(set(tested)):
                return NextQuestion(question_id=question_id)
        return None

    if failed:
        ready = [i for i in failed if progress.last_tested[i] != latest]
        if not ready:
            others = [i for i in key_idea_ids if statuses[i] != KeyIdeaStatus.correct]
            others = [i for i in others if i not in failed]
            if others:
                return queued(lambda t: t & set(others) and not t & set(failed)) or NextQuestion(
                    key_idea_ids=[weakest(progress, others)]
                )
            ready = failed
        # The oldest gap first; key ideas that failed together are checked together.
        first = min(progress.last_tested[i] for i in ready)
        gap = [i for i in ready if progress.last_tested[i] == first]
        return queued(lambda t: t & set(gap)) or NextQuestion(
            key_idea_ids=gap[:MAX_KEY_IDEAS_PER_QUESTION]
        )

    if queue:
        return NextQuestion(question_id=queue[0][0])
    # The plan is used up: open key ideas first; if all are correct but mastery is
    # still too low (or the concept is being reviewed), the weakest one.
    open_ = [i for i in key_idea_ids if statuses[i] != KeyIdeaStatus.correct]
    return NextQuestion(key_idea_ids=[weakest(progress, open_ or key_idea_ids)])


def weakest(progress: Progress, key_idea_ids: list[int]) -> int:
    """The key idea with the lowest average points; untested ones count as 0.

    Ties go to more failed attempts, then to the earlier key idea.
    """

    def key(i: int) -> tuple[float, int]:
        points = progress.points[i]
        return (sum(points) / len(points) if points else 0.0, -progress.failed_attempts[i])

    return min(key_idea_ids, key=key)
