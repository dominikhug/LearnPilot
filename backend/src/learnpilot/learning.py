"""Learning session: questions, answers saved before grading, disputes, re-explanations."""

import logging
from typing import NoReturn

import anthropic
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, select

from learnpilot import learner, llm, tutoring
from learnpilot.auth import CurrentUser
from learnpilot.concepts import LearnerGraph, load_learner_graph
from learnpilot.documents import DbSession
from learnpilot.graph import NodeState
from learnpilot.models import (
    Answer,
    Chunk,
    Concept,
    ConceptStatus,
    Document,
    Explanation,
    ExplanationAngle,
    KeyIdea,
    KeyIdeaStatus,
    LearnerConceptState,
    LearnerKeyIdeaState,
    LlmPurpose,
    Question,
    QuestionLevel,
    QuestionOrigin,
    QuestionState,
    User,
    utcnow,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["learning"])

MAX_ANSWER_CHARS = 10_000
MAX_DISPUTE_CHARS = 2_000


class QuestionOut(BaseModel):
    id: int
    number: int
    text: str
    level: QuestionLevel
    origin: QuestionOrigin
    # One point per tested key idea.
    points: int


class KeyIdeaFeedbackOut(BaseModel):
    id: int
    text: str
    status: KeyIdeaStatus
    feedback: str


class KeyIdeaRefOut(BaseModel):
    id: int
    text: str


class ExplanationOut(BaseModel):
    id: int
    answer_id: int
    key_ideas: list[KeyIdeaRefOut]
    angle: ExplanationAngle
    text: str
    source_chunk_ids: list[int]


class AnswerOut(BaseModel):
    id: int
    question_id: int
    text: str
    graded: bool
    points_possible: float
    points_earned: float | None
    score: float | None
    key_ideas: list[KeyIdeaFeedbackOut]
    source_chunk_ids: list[int]
    dispute_reason: str | None
    regraded: bool
    can_dispute: bool
    # A tested key idea was missing or a misconception: a re-explanation is due.
    needs_explanation: bool
    explanation: ExplanationOut | None


class SessionOut(BaseModel):
    concept_id: int
    concept_name: str
    document_id: int
    mastery: float
    mastered: bool
    key_idea_count: int
    key_ideas_correct: int
    # The question to answer next. Empty after grading: the next one is chosen
    # (and possibly written) when the learner asks for it.
    question: QuestionOut | None
    # An answer to the current question whose grading failed; grading can be retried.
    ungraded_answer: AnswerOut | None


class ConceptRefOut(BaseModel):
    id: int
    name: str
    state: NodeState
    mastery: float


class CompletionOut(BaseModel):
    """The concept was mastered by this grading."""

    mastery: float
    newly_unlocked: list[ConceptRefOut]
    # The automatic choice; none when the whole document is mastered.
    next_concept: ConceptRefOut | None
    document_completed: bool


class WayOutOut(BaseModel):
    """Offered after every third failed attempt on a key idea."""

    key_ideas: list[KeyIdeaRefOut]
    # A missing prerequisite to learn first, or, when all are mastered, one to review.
    prerequisite: ConceptRefOut | None
    # The automatic choice among the other concepts, to come back to this one later.
    other_concept: ConceptRefOut | None


class GradedOut(BaseModel):
    answer: AnswerOut
    mastery_before: float
    session: SessionOut
    completed: CompletionOut | None
    way_out: WayOutOut | None


class AnswerIn(BaseModel):
    text: str = Field(max_length=MAX_ANSWER_CHARS)


class DisputeIn(BaseModel):
    reason: str = Field(max_length=MAX_DISPUTE_CHARS)


@router.post("/concepts/{concept_id}/session")
def start_session(
    concept_id: int, user: CurrentUser, session: DbSession, start_locked: bool = False
) -> SessionOut:
    """Starts or resumes a concept and returns the question to answer.

    The first start plans the questions. A locked concept starts only with
    `start_locked`; once started it is in progress and needs no confirmation again.
    """
    concept, document = owned_concept(session, user, concept_id)
    state = _concept_state(session, user.id, concept_id) or LearnerConceptState(
        user_id=user.id, concept_id=concept_id, status=ConceptStatus.untouched
    )
    if state.status == ConceptStatus.untouched:
        locked = load_learner_graph(session, user.id, document.id).states[concept_id]
        if locked == NodeState.locked and not start_locked:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This concept is locked until its prerequisites are mastered.",
            )
        state.status = ConceptStatus.in_progress
    state.last_seen = utcnow()
    session.add(state)
    session.commit()

    has_questions = session.exec(
        select(func.count())
        .select_from(Question)
        .where(Question.user_id == user.id, Question.concept_id == concept_id)
    ).one()
    if not has_questions:
        _plan_questions(session, user, concept, document)
    question, ungraded = _current_question(session, user, concept, document)
    return _session_out(session, user.id, concept, question, ungraded)


@router.post("/questions/{question_id}/answer")
def answer_question(
    question_id: int, body: AnswerIn, user: CurrentUser, session: DbSession
) -> GradedOut:
    question = session.get(Question, question_id)
    if question is None or question.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    text = body.text.strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The answer is empty.")
    if question.state == QuestionState.answered:
        raise HTTPException(status.HTTP_409_CONFLICT, "This question was already answered.")

    # Saved before grading, so a failed grading never loses the answer.
    answer = Answer(
        question_id=question_id,
        text=text,
        points_possible=len(question.tested_key_idea_ids),
    )
    question.state = QuestionState.answered
    session.add(answer)
    session.commit()
    return _grade(session, user, answer, question)


@router.post("/answers/{answer_id}/grade")
def retry_grading(answer_id: int, user: CurrentUser, session: DbSession) -> GradedOut:
    answer, question = _owned_answer(session, user, answer_id)
    if answer.evaluation is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This answer is already graded.")
    return _grade(session, user, answer, question)


@router.post("/answers/{answer_id}/dispute")
def dispute_grading(
    answer_id: int, body: DisputeIn, user: CurrentUser, session: DbSession
) -> GradedOut:
    """Grades the answer once more with the learner's reason; the second result is final."""
    answer, question = _owned_answer(session, user, answer_id)
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Give a reason.")
    if answer.evaluation is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This answer is not graded yet.")
    if answer.regraded:
        raise HTTPException(status.HTTP_409_CONFLICT, "This answer was already graded again.")
    answer.dispute_reason = reason
    session.commit()
    dispute = tutoring.Dispute(reason, answer.evaluation)
    return _grade(session, user, answer, question, dispute)


@router.post("/answers/{answer_id}/explanation")
def explain_answer(answer_id: int, user: CurrentUser, session: DbSession) -> ExplanationOut:
    """Re-explains the key ideas the answer missed or got wrong; written once per answer.

    A separate call after grading, so the feedback shows without waiting for it and
    a failed explanation never loses the grading.
    """
    answer, question = _owned_answer(session, user, answer_id)
    concept, document = owned_concept(session, user, question.concept_id)
    key_ideas, chunks = _material(session, concept)
    by_id = {k.id: k for k in key_ideas}
    existing = _explanation_of(session, answer_id)
    if existing is not None:
        return _explanation_out(existing, by_id)
    if answer.evaluation is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This answer is not graded yet.")
    graded = [k for k in answer.evaluation["key_ideas"] if k["id"] in by_id]
    gaps = [k for k in graded if KeyIdeaStatus(k["status"]) in learner.FAILED]
    if not gaps:
        raise HTTPException(status.HTTP_409_CONFLICT, "This answer has no gap to explain.")
    gap_ids = [k["id"] for k in gaps]

    # Earlier misconceptions are read from the stored evaluations.
    misconceptions: dict[int, list[str]] = {i: [] for i in gap_ids}
    for earlier_answer in _graded_answers(session, user.id, concept.id):
        if earlier_answer.id == answer_id:
            continue
        for k in earlier_answer.evaluation["key_ideas"]:
            if k["id"] in misconceptions and k["status"] == KeyIdeaStatus.misconception:
                misconceptions[k["id"]].append(k["feedback"])
    earlier = [
        e
        for e in session.exec(
            select(Explanation)
            .where(Explanation.user_id == user.id, Explanation.concept_id == concept.id)
            .order_by(col(Explanation.id))
        )
        if set(e.key_idea_ids) & set(gap_ids)
    ]
    angle = tutoring.choose_angle([e.angle for e in earlier])

    user_id, concept_id, document_id = user.id, concept.id, document.id
    try:
        llm.check_daily_limit(session)
        result = tutoring.explain_gaps(
            context=tutoring.build_context(concept, key_ideas, chunks),
            question_text=question.text,
            answer=answer.text,
            gaps=[
                tutoring.Gap(
                    k["id"], KeyIdeaStatus(k["status"]), k["feedback"], misconceptions[k["id"]]
                )
                for k in gaps
            ],
            earlier=[tutoring.EarlierExplanation(e.angle, e.text) for e in earlier],
            angle=angle,
            chunk_ids=[c.id for c in chunks],
            fallback_chunk_ids=question.source_chunk_ids,
            language=document.language,
        )
    except Exception as e:
        _llm_failed(session, user_id, document_id, LlmPurpose.explanation, e)
    llm.log_call(session, user_id, document_id, LlmPurpose.explanation, result.usage)
    explanation = Explanation(
        user_id=user_id,
        concept_id=concept_id,
        answer_id=answer_id,
        key_idea_ids=gap_ids,
        angle=angle,
        text=result.text,
        source_chunk_ids=result.source_chunk_ids,
    )
    session.add(explanation)
    session.commit()
    return _explanation_out(explanation, by_id)


def owned_concept(session: Session, user: User, concept_id: int) -> tuple[Concept, Document]:
    row = session.exec(
        select(Concept, Document)
        .join(Document, col(Document.id) == Concept.document_id)
        .where(Concept.id == concept_id, Document.user_id == user.id)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Concept not found")
    return row


def _owned_answer(session: Session, user: User, answer_id: int) -> tuple[Answer, Question]:
    row = session.exec(
        select(Answer, Question)
        .join(Question, col(Question.id) == Answer.question_id)
        .where(Answer.id == answer_id, Question.user_id == user.id)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found")
    return row


def _concept_state(session: Session, user_id: int, concept_id: int) -> LearnerConceptState | None:
    return session.get(LearnerConceptState, (user_id, concept_id))


def _explanation_of(session: Session, answer_id: int) -> Explanation | None:
    return session.exec(select(Explanation).where(Explanation.answer_id == answer_id)).first()


def _material(session: Session, concept: Concept) -> tuple[list[KeyIdea], list[Chunk]]:
    key_ideas = list(
        session.exec(
            select(KeyIdea).where(KeyIdea.concept_id == concept.id).order_by(col(KeyIdea.position))
        )
    )
    chunks = list(
        session.exec(
            select(Chunk)
            .where(col(Chunk.id).in_(concept.source_chunk_ids))
            .order_by(col(Chunk.position))
        )
    )
    return key_ideas, chunks


def _graded_answers(session: Session, user_id: int, concept_id: int) -> list[Answer]:
    """The concept's graded answers in the order they were given."""
    return list(
        session.exec(
            select(Answer)
            .join(Question, col(Question.id) == Answer.question_id)
            .where(
                Question.user_id == user_id,
                Question.concept_id == concept_id,
                col(Answer.evaluation).is_not(None),
            )
            .order_by(col(Answer.id))
        )
    )


def _plan_questions(session: Session, user: User, concept: Concept, document: Document) -> None:
    key_ideas, chunks = _material(session, concept)
    user_id, concept_id, document_id = user.id, concept.id, document.id
    try:
        llm.check_daily_limit(session)
        result = tutoring.plan_questions(
            tutoring.build_context(concept, key_ideas, chunks),
            [k.id for k in key_ideas],
            [c.id for c in chunks],
            document.language,
        )
    except Exception as e:
        _llm_failed(session, user_id, document_id, LlmPurpose.question_plan, e)
    llm.log_call(session, user_id, document_id, LlmPurpose.question_plan, result.usage)
    session.add_all(
        Question(
            user_id=user_id,
            concept_id=concept_id,
            position=position,
            text=draft.text,
            level=draft.level,
            tested_key_idea_ids=draft.key_idea_ids,
            source_chunk_ids=draft.source_chunk_ids,
            origin=QuestionOrigin.plan,
            state=QuestionState.planned,
        )
        for position, draft in enumerate(result.questions)
    )
    session.commit()


def _current_question(
    session: Session, user: User, concept: Concept, document: Document
) -> tuple[Question | None, Answer | None]:
    """The question on screen, choosing (or writing) the next one when there is none.

    An answer whose grading failed comes first, then a question already asked, so
    a reload shows the same question.
    """
    in_concept = (Question.user_id == user.id, Question.concept_id == concept.id)
    ungraded = session.exec(
        select(Question, Answer)
        .join(Answer, col(Answer.question_id) == Question.id)
        .where(*in_concept, col(Answer.evaluation).is_(None))
        .order_by(col(Answer.id))
    ).first()
    if ungraded is not None:
        return ungraded
    asked = session.exec(
        select(Question).where(*in_concept, Question.state == QuestionState.asked)
    ).first()
    if asked is not None:
        return asked, None

    key_ideas, chunks = _material(session, concept)
    if not key_ideas:
        return None, None
    questions = list(
        session.exec(select(Question).where(*in_concept).order_by(col(Question.position)))
    )
    choice = learner.next_question(
        [k.id for k in key_ideas],
        [tutoring.grades_of(a.evaluation) for a in _graded_answers(session, user.id, concept.id)],
        [(q.id, q.tested_key_idea_ids) for q in questions if q.state == QuestionState.planned],
    )
    if choice.question_id is not None:
        question = session.get(Question, choice.question_id)
        question.state = QuestionState.asked
        session.commit()
        return question, None

    user_id, concept_id, document_id = user.id, concept.id, document.id
    position = max((q.position for q in questions), default=-1) + 1
    try:
        llm.check_daily_limit(session)
        result = tutoring.write_follow_up(
            context=tutoring.build_context(concept, key_ideas, chunks),
            key_idea_ids=choice.key_idea_ids,
            earlier=[
                tutoring.EarlierQuestion(q.text, q.level, q.tested_key_idea_ids) for q in questions
            ],
            chunk_ids=[c.id for c in chunks],
            language=document.language,
        )
    except Exception as e:
        _llm_failed(session, user_id, document_id, LlmPurpose.follow_up_question, e)
    llm.log_call(session, user_id, document_id, LlmPurpose.follow_up_question, result.usage)
    draft = result.question
    question = Question(
        user_id=user_id,
        concept_id=concept_id,
        position=position,
        text=draft.text,
        level=draft.level,
        tested_key_idea_ids=draft.key_idea_ids,
        source_chunk_ids=draft.source_chunk_ids,
        origin=QuestionOrigin.follow_up,
        state=QuestionState.asked,
    )
    session.add(question)
    session.commit()
    return question, None


def _grade(
    session: Session,
    user: User,
    answer: Answer,
    question: Question,
    dispute: tutoring.Dispute | None = None,
) -> GradedOut:
    concept, document = owned_concept(session, user, question.concept_id)
    key_ideas, chunks = _material(session, concept)
    state = _concept_state(session, user.id, concept.id)
    mastery_before = state.mastery if state else 0.0
    was_mastered = state is not None and state.status == ConceptStatus.mastered
    # Key ideas edited or deleted since the question was answered are not graded again.
    tested = [i for i in question.tested_key_idea_ids if i in {k.id for k in key_ideas}]
    if not tested:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The key ideas this question tested were changed."
        )
    answer.points_possible = len(tested)
    user_id, document_id = user.id, document.id
    try:
        llm.check_daily_limit(session)
        result = tutoring.grade_answer(
            context=tutoring.build_context(concept, key_ideas, chunks),
            question_text=question.text,
            tested_key_idea_ids=tested,
            answer=answer.text,
            language=document.language,
            dispute=dispute,
        )
    except Exception as e:
        _llm_failed(session, user_id, document_id, LlmPurpose.grading, e)
    llm.log_call(session, user_id, document_id, LlmPurpose.grading, result.usage)

    # Points and score are computed in code, never by the model.
    grades = tutoring.grades_of(result.evaluation)
    answer.evaluation = result.evaluation
    answer.points_earned = learner.points_earned(grades)
    answer.score = learner.score(grades)
    answer.regraded = answer.regraded or dispute is not None
    session.flush()
    progress, answers = update_progress(session, user_id, concept.id, key_ideas)
    session.commit()

    key_ideas_by_id = {k.id: k for k in key_ideas}
    session_out = _session_out(session, user_id, concept, None, None)
    learner_graph = None
    completed = way_out = None
    if session_out.mastered and not was_mastered:
        learner_graph = load_learner_graph(session, user_id, document_id)
        completed = _completion(learner_graph, concept.id)
    index = next(i for i, a in enumerate(answers) if a.id == answer.id)
    # Only when this answer is the key idea's latest; a disputed older answer is not.
    stuck = [
        k
        for k in key_ideas
        if k.id in grades
        and progress.last_tested.get(k.id) == index
        and grades[k.id] in learner.FAILED
        and learner.reached_attempt_limit(progress, k.id)
    ]
    if stuck:
        learner_graph = learner_graph or load_learner_graph(session, user_id, document_id)
        way_out = _way_out(learner_graph, concept.id, stuck)

    return GradedOut(
        answer=_answer_out(answer, question, key_ideas_by_id, _explanation_of(session, answer.id)),
        mastery_before=mastery_before,
        session=session_out,
        completed=completed,
        way_out=way_out,
    )


def _concept_ref(learner_graph: LearnerGraph, concept_id: int | None) -> ConceptRefOut | None:
    if concept_id is None:
        return None
    name = next(c.name for c in learner_graph.concepts if c.id == concept_id)
    return ConceptRefOut(
        id=concept_id,
        name=name,
        state=learner_graph.states[concept_id],
        mastery=learner_graph.mastery(concept_id),
    )


def _completion(learner_graph: LearnerGraph, concept_id: int) -> CompletionOut:
    states = learner_graph.states
    # Dependents were locked until now; the ones started early stay "in progress".
    unlocked = [d for d in learner_graph.dependents(concept_id) if states[d] == NodeState.unlocked]
    return CompletionOut(
        mastery=learner_graph.mastery(concept_id),
        newly_unlocked=[_concept_ref(learner_graph, i) for i in unlocked],
        next_concept=_concept_ref(learner_graph, learner_graph.choose()),
        document_completed=all(s == NodeState.mastered for s in states.values()),
    )


def _way_out(learner_graph: LearnerGraph, concept_id: int, stuck: list[KeyIdea]) -> WayOutOut:
    prerequisite = learner_graph.learn_first(concept_id)
    if prerequisite is None:
        # All prerequisites are mastered: suggest reviewing the weakest one.
        prerequisite = min(
            learner_graph.prerequisites(concept_id), key=learner_graph.mastery, default=None
        )
    others = [i for i in learner_graph.ids if i != concept_id]
    return WayOutOut(
        key_ideas=[KeyIdeaRefOut(id=k.id, text=k.text) for k in stuck],
        prerequisite=_concept_ref(learner_graph, prerequisite),
        other_concept=_concept_ref(learner_graph, learner_graph.choose(others)),
    )


def _llm_failed(
    session: Session, user_id: int, document_id: int, purpose: LlmPurpose, error: Exception
) -> NoReturn:
    """Turns a failed AI call into a message for the user; failed calls cost tokens too."""
    message = llm.error_message(error)
    if message is None:
        raise error
    if isinstance(error, llm.DailyLimitReachedError):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, message) from error
    if isinstance(error, llm.LlmOutputError):
        llm.log_call(session, user_id, document_id, purpose, error.usage)
    elif isinstance(error, anthropic.APIError):
        log.exception("AI service call failed (%s)", purpose)
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, message) from error


def update_progress(
    session: Session, user_id: int, concept_id: int, key_ideas: list[KeyIdea], seen: bool = True
) -> tuple[learner.Progress, list[Answer]]:
    """Rebuilds the concept's learner state from its answer history.

    A full replay rather than an increment, so a re-graded answer in the middle
    of the history is accounted for in the right order. `seen` is false after a
    graph edit, which must not count as having worked on the concept.
    """
    answers = _graded_answers(session, user_id, concept_id)
    progress = learner.replay(
        [k.id for k in key_ideas], [tutoring.grades_of(a.evaluation) for a in answers]
    )

    state = _concept_state(session, user_id, concept_id) or LearnerConceptState(
        user_id=user_id, concept_id=concept_id, status=ConceptStatus.in_progress
    )
    state.mastery = progress.mastery
    if seen:
        state.last_seen = utcnow()
    # Mastered is permanent; it is only ever set here, never taken back.
    if progress.mastered_after is not None and state.status != ConceptStatus.mastered:
        state.status = ConceptStatus.mastered
        state.mastered_at = answers[progress.mastered_after].created_at
    session.add(state)

    rows = {
        row.key_idea_id: row
        for row in session.exec(
            select(LearnerKeyIdeaState).where(
                LearnerKeyIdeaState.user_id == user_id,
                col(LearnerKeyIdeaState.key_idea_id).in_(progress.statuses),
            )
        )
    }
    for key_idea_id, key_idea_status in progress.statuses.items():
        row = rows.get(key_idea_id) or LearnerKeyIdeaState(
            user_id=user_id, key_idea_id=key_idea_id, status=key_idea_status
        )
        row.status = key_idea_status
        row.failed_attempts = progress.failed_attempts[key_idea_id]
        session.add(row)
    return progress, answers


def _session_out(
    session: Session,
    user_id: int,
    concept: Concept,
    question: Question | None,
    ungraded: Answer | None,
) -> SessionOut:
    state = _concept_state(session, user_id, concept.id)
    key_idea_ids = list(session.exec(select(KeyIdea.id).where(KeyIdea.concept_id == concept.id)))
    correct = session.exec(
        select(func.count())
        .select_from(LearnerKeyIdeaState)
        .where(
            LearnerKeyIdeaState.user_id == user_id,
            col(LearnerKeyIdeaState.key_idea_id).in_(key_idea_ids),
            LearnerKeyIdeaState.status == KeyIdeaStatus.correct,
        )
    ).one()
    question_out = None
    if question is not None:
        # Numbered in the order asked; checks and follow-ups can change the plan order.
        answered_before = session.exec(
            select(func.count())
            .select_from(Question)
            .where(
                Question.user_id == user_id,
                Question.concept_id == concept.id,
                Question.state == QuestionState.answered,
                Question.id != question.id,
            )
        ).one()
        question_out = QuestionOut(
            id=question.id,
            number=answered_before + 1,
            text=question.text,
            level=question.level,
            origin=question.origin,
            points=len(question.tested_key_idea_ids),
        )

    return SessionOut(
        concept_id=concept.id,
        concept_name=concept.name,
        document_id=concept.document_id,
        mastery=state.mastery if state else 0.0,
        mastered=state is not None and state.status == ConceptStatus.mastered,
        key_idea_count=len(key_idea_ids),
        key_ideas_correct=correct,
        question=question_out,
        ungraded_answer=None
        if ungraded is None
        else _answer_out(ungraded, question, key_ideas={}, explanation=None),
    )


def _explanation_out(explanation: Explanation, key_ideas: dict[int, KeyIdea]) -> ExplanationOut:
    return ExplanationOut(
        id=explanation.id,
        answer_id=explanation.answer_id,
        key_ideas=[
            KeyIdeaRefOut(id=i, text=key_ideas[i].text)
            for i in explanation.key_idea_ids
            if i in key_ideas
        ],
        angle=explanation.angle,
        text=explanation.text,
        source_chunk_ids=explanation.source_chunk_ids,
    )


def _answer_out(
    answer: Answer,
    question: Question,
    key_ideas: dict[int, KeyIdea],
    explanation: Explanation | None,
) -> AnswerOut:
    evaluation = answer.evaluation or {"key_ideas": []}
    graded = [k for k in evaluation["key_ideas"] if k["id"] in key_ideas]
    return AnswerOut(
        id=answer.id,
        question_id=answer.question_id,
        text=answer.text,
        graded=answer.evaluation is not None,
        points_possible=answer.points_possible,
        points_earned=answer.points_earned,
        score=answer.score,
        key_ideas=[
            KeyIdeaFeedbackOut(
                id=k["id"], text=key_ideas[k["id"]].text, status=k["status"], feedback=k["feedback"]
            )
            for k in graded
        ],
        source_chunk_ids=question.source_chunk_ids,
        dispute_reason=answer.dispute_reason,
        regraded=answer.regraded,
        can_dispute=answer.evaluation is not None and not answer.regraded,
        needs_explanation=any(KeyIdeaStatus(k["status"]) in learner.FAILED for k in graded),
        explanation=None if explanation is None else _explanation_out(explanation, key_ideas),
    )
