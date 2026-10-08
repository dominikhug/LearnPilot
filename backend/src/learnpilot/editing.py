"""Graph editing (desktop): rename and delete concepts, edges, key ideas.

"Unlocked" is derived from the graph, so edge changes need no follow-up here.
Key idea changes do: learner progress is replayed from the graded answers, so
it is rebuilt after every change.
"""

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, select

from learnpilot import graph
from learnpilot.auth import CurrentUser
from learnpilot.concepts import EdgeOut
from learnpilot.documents import DbSession
from learnpilot.learning import owned_concept, update_progress
from learnpilot.models import (
    Answer,
    Concept,
    Document,
    KeyIdea,
    KeyIdeaStatus,
    LearnerConceptState,
    LearnerKeyIdeaState,
    PrerequisiteEdge,
    Question,
    User,
)

router = APIRouter(prefix="/api", tags=["editing"])

MAX_NAME_CHARS = 200
MAX_KEY_IDEA_CHARS = 1_000
# An edge the user added is certain, unlike the model's guesses.
USER_EDGE_CONFIDENCE = 1.0


class ConceptIn(BaseModel):
    name: str = Field(max_length=MAX_NAME_CHARS)


class PrerequisiteIn(BaseModel):
    prerequisite_id: int


class KeyIdeaIn(BaseModel):
    text: str = Field(max_length=MAX_KEY_IDEA_CHARS)


class KeyIdeaOut(BaseModel):
    id: int
    text: str
    status: KeyIdeaStatus


def _required(text: str, message: str) -> str:
    text = text.strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)
    return text


def _no_content() -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/concepts/{concept_id}", status_code=status.HTTP_204_NO_CONTENT)
def rename_concept(
    concept_id: int, body: ConceptIn, user: CurrentUser, session: DbSession
) -> Response:
    concept, _ = owned_concept(session, user, concept_id)
    concept.name = _required(body.name, "Give the concept a name.")
    session.commit()
    return _no_content()


@router.delete("/concepts/{concept_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_concept(concept_id: int, user: CurrentUser, session: DbSession) -> Response:
    # Key ideas, questions, answers, explanations, learner state and edges go via
    # ON DELETE CASCADE. Dependents may become unlocked, which is derived.
    concept, _ = owned_concept(session, user, concept_id)
    session.delete(concept)
    session.commit()
    return _no_content()


@router.post("/concepts/{concept_id}/prerequisites", status_code=status.HTTP_201_CREATED)
def add_prerequisite(
    concept_id: int, body: PrerequisiteIn, user: CurrentUser, session: DbSession
) -> EdgeOut:
    """Adds an edge; one that would close a cycle is rejected, so the graph stays a DAG."""
    concept, _ = owned_concept(session, user, concept_id)
    prerequisite = session.get(Concept, body.prerequisite_id)
    if prerequisite is None or prerequisite.document_id != concept.document_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Concept not found")
    if prerequisite.id == concept.id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "A concept cannot be its own prerequisite."
        )
    edges = list(
        session.exec(
            select(PrerequisiteEdge.from_concept_id, PrerequisiteEdge.to_concept_id)
            .join(Concept, col(Concept.id) == PrerequisiteEdge.to_concept_id)
            .where(Concept.document_id == concept.document_id)
        ).all()
    )
    if (prerequisite.id, concept.id) in edges:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{prerequisite.name} is already a prerequisite."
        )
    if graph.has_path(concept.id, prerequisite.id, edges):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{prerequisite.name} builds on {concept.name}, so it cannot also be its prerequisite.",
        )
    edge = PrerequisiteEdge(
        from_concept_id=prerequisite.id,
        to_concept_id=concept.id,
        confidence=USER_EDGE_CONFIDENCE,
    )
    session.add(edge)
    session.commit()
    return EdgeOut.model_validate(edge, from_attributes=True)


@router.delete(
    "/concepts/{concept_id}/prerequisites/{prerequisite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_prerequisite(
    concept_id: int, prerequisite_id: int, user: CurrentUser, session: DbSession
) -> Response:
    owned_concept(session, user, concept_id)
    edge = session.get(PrerequisiteEdge, (prerequisite_id, concept_id))
    if edge is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prerequisite not found")
    session.delete(edge)
    session.commit()
    return _no_content()


@router.get("/concepts/{concept_id}/key-ideas")
def list_key_ideas(concept_id: int, user: CurrentUser, session: DbSession) -> list[KeyIdeaOut]:
    """All key ideas, untested ones included: editing reveals the answer rubric."""
    owned_concept(session, user, concept_id)
    rows = session.exec(
        select(KeyIdea, LearnerKeyIdeaState.status)
        .join(
            LearnerKeyIdeaState,
            (col(LearnerKeyIdeaState.key_idea_id) == KeyIdea.id)
            & (LearnerKeyIdeaState.user_id == user.id),
            isouter=True,
        )
        .where(KeyIdea.concept_id == concept_id)
        .order_by(col(KeyIdea.position))
    )
    return [
        KeyIdeaOut(id=k.id, text=k.text, status=key_idea_status or KeyIdeaStatus.untested)
        for k, key_idea_status in rows
    ]


@router.patch("/key-ideas/{key_idea_id}")
def edit_key_idea(
    key_idea_id: int, body: KeyIdeaIn, user: CurrentUser, session: DbSession
) -> KeyIdeaOut:
    """Changes the text, which resets the status to untested.

    The key idea is replaced by a new one, so grades given against the old text no
    longer count, even when an older answer is graded again after a dispute.
    """
    key_idea = _owned_key_idea(session, user, key_idea_id)
    text = _required(body.text, "A key idea cannot be empty.")
    if text == key_idea.text:
        return KeyIdeaOut(id=key_idea.id, text=text, status=_status(session, user, key_idea.id))
    replacement = KeyIdea(concept_id=key_idea.concept_id, position=key_idea.position, text=text)
    session.add(replacement)
    session.flush()
    replacement_id = replacement.id
    _retire(session, key_idea, replacement_id)
    return KeyIdeaOut(id=replacement_id, text=text, status=KeyIdeaStatus.untested)


@router.delete("/key-ideas/{key_idea_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_key_idea(key_idea_id: int, user: CurrentUser, session: DbSession) -> Response:
    key_idea = _owned_key_idea(session, user, key_idea_id)
    remaining = session.exec(
        select(func.count()).select_from(KeyIdea).where(KeyIdea.concept_id == key_idea.concept_id)
    ).one()
    if remaining == 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A concept needs at least one key idea. Delete the concept instead.",
        )
    _retire(session, key_idea, None)
    return _no_content()


def _owned_key_idea(session: Session, user: User, key_idea_id: int) -> KeyIdea:
    key_idea = session.exec(
        select(KeyIdea)
        .join(Concept, col(Concept.id) == KeyIdea.concept_id)
        .join(Document, col(Document.id) == Concept.document_id)
        .where(KeyIdea.id == key_idea_id, Document.user_id == user.id)
    ).first()
    if key_idea is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key idea not found")
    return key_idea


def _status(session: Session, user: User, key_idea_id: int) -> KeyIdeaStatus:
    state = session.get(LearnerKeyIdeaState, (user.id, key_idea_id))
    return state.status if state else KeyIdeaStatus.untested


def _retire(session: Session, key_idea: KeyIdea, replacement_id: int | None) -> None:
    """Deletes a key idea, moving questions not graded yet to its replacement.

    Without a replacement those questions stop testing it; one left testing
    nothing is deleted, with an answer whose grading failed. Graded answers keep
    their evaluation; the replay skips key ideas that no longer exist.
    """
    concept_id, retired_id = key_idea.concept_id, key_idea.id
    ungraded = session.exec(
        select(Question, Answer)
        .join(Answer, col(Answer.question_id) == Question.id, isouter=True)
        .where(Question.concept_id == concept_id, col(Answer.evaluation).is_(None))
    )
    for question, answer in ungraded:
        if retired_id not in question.tested_key_idea_ids:
            continue
        replaced = (replacement_id if i == retired_id else i for i in question.tested_key_idea_ids)
        tested = [i for i in dict.fromkeys(replaced) if i is not None]
        if not tested:
            session.delete(question)
            continue
        # A new list, so the JSON column registers the change.
        question.tested_key_idea_ids = tested
        if answer is not None:
            answer.points_possible = len(tested)
    session.delete(key_idea)
    session.flush()

    key_ideas = list(
        session.exec(
            select(KeyIdea).where(KeyIdea.concept_id == concept_id).order_by(col(KeyIdea.position))
        )
    )
    learners = session.exec(
        select(LearnerConceptState.user_id).where(LearnerConceptState.concept_id == concept_id)
    ).all()
    for user_id in learners:
        update_progress(session, user_id, concept_id, key_ideas, seen=False)
    session.commit()
