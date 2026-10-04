from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import col, select

from learnpilot import graph
from learnpilot.auth import CurrentUser
from learnpilot.documents import DbSession, owned_document
from learnpilot.models import (
    Concept,
    ConceptStatus,
    KeyIdea,
    KeyIdeaStatus,
    LearnerConceptState,
    LearnerKeyIdeaState,
    PrerequisiteEdge,
)

router = APIRouter(prefix="/api/documents", tags=["concepts"])


class TestedKeyIdeaOut(BaseModel):
    id: int
    text: str
    status: KeyIdeaStatus


class ConceptOut(BaseModel):
    id: int
    name: str
    definition: str
    level: int
    state: graph.NodeState
    mastery: float
    prerequisite_ids: list[int]
    source_chunk_ids: list[int]
    # Untested key ideas stay hidden: they are the answer rubric.
    key_idea_count: int
    tested_key_ideas: list[TestedKeyIdeaOut]


class EdgeOut(BaseModel):
    from_concept_id: int
    to_concept_id: int
    confidence: float


class GraphOut(BaseModel):
    concepts: list[ConceptOut]
    edges: list[EdgeOut]


@router.get("/{document_id}/graph")
def get_graph(document_id: int, user: CurrentUser, session: DbSession) -> GraphOut:
    owned_document(session, user, document_id)
    concepts = list(
        session.exec(
            select(Concept)
            .where(Concept.document_id == document_id)
            .order_by(col(Concept.position))
        )
    )
    ids = [c.id for c in concepts]
    edges = list(
        session.exec(select(PrerequisiteEdge).where(col(PrerequisiteEdge.to_concept_id).in_(ids)))
    )
    key_ideas: dict[int, list[KeyIdea]] = {i: [] for i in ids}
    for key_idea in session.exec(
        select(KeyIdea).where(col(KeyIdea.concept_id).in_(ids)).order_by(col(KeyIdea.position))
    ):
        key_ideas[key_idea.concept_id].append(key_idea)
    key_idea_states = dict(
        session.exec(
            select(LearnerKeyIdeaState.key_idea_id, LearnerKeyIdeaState.status).where(
                LearnerKeyIdeaState.user_id == user.id,
                col(LearnerKeyIdeaState.key_idea_id).in_(
                    [k.id for group in key_ideas.values() for k in group]
                ),
                LearnerKeyIdeaState.status != KeyIdeaStatus.untested,
            )
        ).all()
    )
    learner_states = {
        s.concept_id: s
        for s in session.exec(
            select(LearnerConceptState).where(
                LearnerConceptState.user_id == user.id,
                col(LearnerConceptState.concept_id).in_(ids),
            )
        )
    }

    pairs = [(e.from_concept_id, e.to_concept_id) for e in edges]
    levels = graph.levels(ids, pairs)
    states = graph.node_states(
        ids,
        pairs,
        mastered={i for i, s in learner_states.items() if s.status == ConceptStatus.mastered},
        in_progress={i for i, s in learner_states.items() if s.status == ConceptStatus.in_progress},
    )
    prerequisites: dict[int, list[int]] = {i: [] for i in ids}
    for a, b in pairs:
        prerequisites[b].append(a)

    return GraphOut(
        concepts=[
            ConceptOut(
                id=c.id,
                name=c.name,
                definition=c.definition,
                level=levels[c.id],
                state=states[c.id],
                mastery=learner_states[c.id].mastery if c.id in learner_states else 0.0,
                prerequisite_ids=prerequisites[c.id],
                source_chunk_ids=c.source_chunk_ids,
                key_idea_count=len(key_ideas[c.id]),
                tested_key_ideas=[
                    TestedKeyIdeaOut(id=k.id, text=k.text, status=key_idea_states[k.id])
                    for k in key_ideas[c.id]
                    if k.id in key_idea_states
                ],
            )
            for c in concepts
        ],
        edges=[EdgeOut.model_validate(e, from_attributes=True) for e in edges],
    )
