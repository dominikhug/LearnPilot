"""Learning session: question plan, answers saved before grading, disputes."""

import logging
from typing import NoReturn

import anthropic
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, select

from learnpilot import learner, llm, tutoring
from learnpilot.auth import CurrentUser
from learnpilot.documents import DbSession
from learnpilot.models import (
    Answer,
    Chunk,
    Concept,
    ConceptStatus,
    Document,
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
    # One point per tested key idea.
    points: int


class KeyIdeaFeedbackOut(BaseModel):
    id: int
    text: str
    status: KeyIdeaStatus
    feedback: str


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


class SessionOut(BaseModel):
    concept_id: int
    concept_name: str
    document_id: int
    mastery: float
    mastered: bool
    key_idea_count: int
    key_ideas_correct: int
    # The question to answer next; none when the plan is used up.
    question: QuestionOut | None
    # An answer to the current question whose grading failed; grading can be retried.
    ungraded_answer: AnswerOut | None


class GradedOut(BaseModel):
    answer: AnswerOut
    mastery_before: float
    session: SessionOut


class AnswerIn(BaseModel):
    text: str = Field(max_length=MAX_ANSWER_CHARS)


class DisputeIn(BaseModel):
    reason: str = Field(max_length=MAX_DISPUTE_CHARS)


@router.post("/concepts/{concept_id}/session")
def start_session(concept_id: int, user: CurrentUser, session: DbSession) -> SessionOut:
    """Starts or resumes a concept. The first start plans its questions."""
    concept, document = _owned_concept(session, user, concept_id)
    state = _concept_state(session, user.id, concept_id) or LearnerConceptState(
        user_id=user.id, concept_id=concept_id, status=ConceptStatus.untouched
    )
    if state.status == ConceptStatus.untouched:
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
    return _session_out(session, user.id, concept)


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


def _owned_concept(session: Session, user: User, concept_id: int) -> tuple[Concept, Document]:
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


def _plan_questions(session: Session, user: User, concept: Concept, document: Document) -> None:
    key_ideas, chunks = _material(session, concept)
    user_id, concept_id, document_id = user.id, concept.id, document.id
    try:
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


def _grade(
    session: Session,
    user: User,
    answer: Answer,
    question: Question,
    dispute: tutoring.Dispute | None = None,
) -> GradedOut:
    concept, document = _owned_concept(session, user, question.concept_id)
    key_ideas, chunks = _material(session, concept)
    state = _concept_state(session, user.id, concept.id)
    mastery_before = state.mastery if state else 0.0
    user_id, document_id = user.id, document.id
    try:
        result = tutoring.grade_answer(
            context=tutoring.build_context(concept, key_ideas, chunks),
            question_text=question.text,
            tested_key_idea_ids=question.tested_key_idea_ids,
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
    _update_progress(session, user_id, concept.id, key_ideas)
    session.commit()

    return GradedOut(
        answer=_answer_out(answer, question, {k.id: k for k in key_ideas}),
        mastery_before=mastery_before,
        session=_session_out(session, user_id, concept),
    )


def _llm_failed(
    session: Session, user_id: int, document_id: int, purpose: LlmPurpose, error: Exception
) -> NoReturn:
    """Turns a failed AI call into a message for the user; failed calls cost tokens too."""
    message = llm.error_message(error)
    if message is None:
        raise error
    if isinstance(error, llm.LlmOutputError):
        llm.log_call(session, user_id, document_id, purpose, error.usage)
    elif isinstance(error, anthropic.APIError):
        log.exception("AI service call failed (%s)", purpose)
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, message) from error


def _update_progress(
    session: Session, user_id: int, concept_id: int, key_ideas: list[KeyIdea]
) -> None:
    """Rebuilds the concept's learner state from its answer history.

    A full replay rather than an increment, so a re-graded answer in the middle
    of the history is accounted for in the right order.
    """
    answers = list(
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
    progress = learner.replay(
        [k.id for k in key_ideas], [tutoring.grades_of(a.evaluation) for a in answers]
    )

    state = _concept_state(session, user_id, concept_id) or LearnerConceptState(
        user_id=user_id, concept_id=concept_id, status=ConceptStatus.in_progress
    )
    state.mastery = progress.mastery
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


def _session_out(session: Session, user_id: int, concept: Concept) -> SessionOut:
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

    question, ungraded = _open_question(session, user_id, concept.id)
    if question is not None and question.state == QuestionState.planned:
        question.state = QuestionState.asked
        session.commit()

    return SessionOut(
        concept_id=concept.id,
        concept_name=concept.name,
        document_id=concept.document_id,
        mastery=state.mastery if state else 0.0,
        mastered=state is not None and state.status == ConceptStatus.mastered,
        key_idea_count=len(key_idea_ids),
        key_ideas_correct=correct,
        question=None
        if question is None
        else QuestionOut(
            id=question.id,
            number=question.position + 1,
            text=question.text,
            level=question.level,
            points=len(question.tested_key_idea_ids),
        ),
        ungraded_answer=None if ungraded is None else _answer_out(ungraded, question, key_ideas={}),
    )


def _open_question(
    session: Session, user_id: int, concept_id: int
) -> tuple[Question | None, Answer | None]:
    """An answer whose grading failed comes first; otherwise the next unanswered question."""
    in_concept = (Question.user_id == user_id, Question.concept_id == concept_id)
    ungraded = session.exec(
        select(Question, Answer)
        .join(Answer, col(Answer.question_id) == Question.id)
        .where(*in_concept, col(Answer.evaluation).is_(None))
        .order_by(col(Answer.id))
    ).first()
    if ungraded is not None:
        return ungraded
    question = session.exec(
        select(Question)
        .where(*in_concept, Question.state != QuestionState.answered)
        .order_by(col(Question.position))
    ).first()
    return question, None


def _answer_out(answer: Answer, question: Question, key_ideas: dict[int, KeyIdea]) -> AnswerOut:
    evaluation = answer.evaluation or {"key_ideas": []}
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
            for k in evaluation["key_ideas"]
            if k["id"] in key_ideas
        ],
        source_chunk_ids=question.source_chunk_ids,
        dispute_reason=answer.dispute_reason,
        regraded=answer.regraded,
        can_dispute=answer.evaluation is not None and not answer.regraded,
    )
