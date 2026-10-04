from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import col, func, select

from learnpilot import graph
from learnpilot.auth import CurrentUser
from learnpilot.documents import DbSession, owned_document
from learnpilot.models import Concept, KeyIdea, PrerequisiteEdge

router = APIRouter(prefix="/api/documents", tags=["concepts"])


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
    key_idea_counts = dict(
        session.exec(
            select(KeyIdea.concept_id, func.count())
            .where(col(KeyIdea.concept_id).in_(ids))
            .group_by(col(KeyIdea.concept_id))
        ).all()
    )

    pairs = [(e.from_concept_id, e.to_concept_id) for e in edges]
    levels = graph.levels(ids, pairs)
    # Learner progress arrives with the learning session; until then nothing is mastered.
    states = graph.node_states(ids, pairs)
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
                mastery=0.0,
                prerequisite_ids=prerequisites[c.id],
                source_chunk_ids=c.source_chunk_ids,
                key_idea_count=key_idea_counts.get(c.id, 0),
            )
            for c in concepts
        ],
        edges=[EdgeOut.model_validate(e, from_attributes=True) for e in edges],
    )
