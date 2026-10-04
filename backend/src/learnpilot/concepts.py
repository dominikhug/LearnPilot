from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import Session, col, select

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
    # Locked concepts only: the missing prerequisite to learn first.
    learn_first_id: int | None


class EdgeOut(BaseModel):
    from_concept_id: int
    to_concept_id: int
    confidence: float


class GraphOut(BaseModel):
    concepts: list[ConceptOut]
    edges: list[EdgeOut]
    # The automatic choice for "Start learning"; none when everything is mastered.
    next_concept_id: int | None


@dataclass
class LearnerGraph:
    """A document's concepts and prerequisite edges with one learner's progress."""

    concepts: list[Concept]
    edge_rows: list[PrerequisiteEdge]
    learner_states: dict[int, LearnerConceptState]
    states: dict[int, graph.NodeState]

    @property
    def ids(self) -> list[int]:
        return [c.id for c in self.concepts]

    @property
    def edges(self) -> list[tuple[int, int]]:
        return [(e.from_concept_id, e.to_concept_id) for e in self.edge_rows]

    def mastery(self, concept_id: int) -> float:
        state = self.learner_states.get(concept_id)
        return state.mastery if state else 0.0

    def choose(self, candidates: list[int] | None = None) -> int | None:
        last_seen: dict[int, datetime] = {i: s.last_seen for i, s in self.learner_states.items()}
        mastery = {i: s.mastery for i, s in self.learner_states.items()}
        return graph.choose_next(self.ids, self.edges, self.states, mastery, last_seen, candidates)

    def learn_first(self, concept_id: int) -> int | None:
        """The missing prerequisite the automatic choice would pick."""
        missing = graph.missing_prerequisites(concept_id, self.ids, self.edges, self.states)
        return self.choose([i for i in self.ids if i in missing]) if missing else None

    def prerequisites(self, concept_id: int) -> list[int]:
        return [a for a, b in self.edges if b == concept_id]

    def dependents(self, concept_id: int) -> list[int]:
        return [b for a, b in self.edges if a == concept_id]


def load_learner_graph(session: Session, user_id: int, document_id: int) -> LearnerGraph:
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
    learner_states = {
        s.concept_id: s
        for s in session.exec(
            select(LearnerConceptState).where(
                LearnerConceptState.user_id == user_id,
                col(LearnerConceptState.concept_id).in_(ids),
            )
        )
    }
    states = graph.node_states(
        ids,
        [(e.from_concept_id, e.to_concept_id) for e in edges],
        mastered={i for i, s in learner_states.items() if s.status == ConceptStatus.mastered},
        in_progress={i for i, s in learner_states.items() if s.status == ConceptStatus.in_progress},
    )
    return LearnerGraph(concepts, edges, learner_states, states)


@router.get("/{document_id}/graph")
def get_graph(document_id: int, user: CurrentUser, session: DbSession) -> GraphOut:
    owned_document(session, user, document_id)
    learner_graph = load_learner_graph(session, user.id, document_id)
    ids = learner_graph.ids
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
    levels = graph.levels(ids, learner_graph.edges)
    states = learner_graph.states

    return GraphOut(
        concepts=[
            ConceptOut(
                id=c.id,
                name=c.name,
                definition=c.definition,
                level=levels[c.id],
                state=states[c.id],
                mastery=learner_graph.mastery(c.id),
                prerequisite_ids=learner_graph.prerequisites(c.id),
                source_chunk_ids=c.source_chunk_ids,
                key_idea_count=len(key_ideas[c.id]),
                tested_key_ideas=[
                    TestedKeyIdeaOut(id=k.id, text=k.text, status=key_idea_states[k.id])
                    for k in key_ideas[c.id]
                    if k.id in key_idea_states
                ],
                learn_first_id=learner_graph.learn_first(c.id)
                if states[c.id] == graph.NodeState.locked
                else None,
            )
            for c in learner_graph.concepts
        ],
        edges=[EdgeOut.model_validate(e, from_attributes=True) for e in learner_graph.edge_rows],
        next_concept_id=learner_graph.choose(),
    )
