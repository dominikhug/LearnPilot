from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, Column, DateTime, Enum, Float, ForeignKey, Integer, Text
from sqlmodel import Field, SQLModel

DEFAULT_USER_ID = 1


def utcnow() -> datetime:
    return datetime.now(UTC)


def enum_column(enum: type[StrEnum]) -> Column:
    # VARCHAR without a CHECK constraint, so adding a value needs no migration.
    return Column(
        Enum(
            enum,
            native_enum=False,
            create_constraint=False,
            length=32,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str


class DocumentStatus(StrEnum):
    processing = "processing"
    ready = "ready"
    failed = "failed"


class ProcessingStep(StrEnum):
    """Background steps after upload; text extraction happens in the upload request."""

    counting_tokens = "counting_tokens"
    extracting_concepts = "extracting_concepts"
    building_graph = "building_graph"
    done = "done"


class Document(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    title: str
    token_count: int | None = None
    language: str | None = None
    status: DocumentStatus = Field(sa_column=enum_column(DocumentStatus))
    step: ProcessingStep = Field(sa_column=enum_column(ProcessingStep))
    error_message: str | None = None
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class Chunk(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("document.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    position: int
    page: int | None = None
    section: str | None = None
    text: str


class Concept(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("document.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    position: int
    name: str
    definition: str
    # JSON rather than a PostgreSQL array, so the tests can run on SQLite.
    source_chunk_ids: list[int] = Field(sa_column=Column(JSON, nullable=False))


class KeyIdea(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    concept_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("concept.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    position: int
    text: str


class PrerequisiteEdge(SQLModel, table=True):
    """from_concept is a prerequisite of to_concept."""

    from_concept_id: int = Field(
        sa_column=Column(Integer, ForeignKey("concept.id", ondelete="CASCADE"), primary_key=True)
    )
    to_concept_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("concept.id", ondelete="CASCADE"), primary_key=True, index=True
        )
    )
    confidence: float = Field(sa_column=Column(Float, nullable=False))


class LlmPurpose(StrEnum):
    concept_extraction = "concept_extraction"
    question_plan = "question_plan"
    grading = "grading"


class LlmCall(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    # Kept when the document is deleted, so the daily token limit still counts it.
    document_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("document.id", ondelete="SET NULL"), index=True),
    )
    purpose: LlmPurpose = Field(sa_column=enum_column(LlmPurpose))
    model: str
    input_tokens: int
    output_tokens: int
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )


class ConceptStatus(StrEnum):
    untouched = "untouched"
    in_progress = "in_progress"
    mastered = "mastered"


class LearnerConceptState(SQLModel, table=True):
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    )
    concept_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("concept.id", ondelete="CASCADE"), primary_key=True, index=True
        )
    )
    status: ConceptStatus = Field(sa_column=enum_column(ConceptStatus))
    mastery: float = Field(default=0.0, sa_column=Column(Float, nullable=False))
    mastered_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    last_seen: datetime = Field(
        default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class KeyIdeaStatus(StrEnum):
    untested = "untested"
    correct = "correct"
    partial = "partial"
    missing = "missing"
    misconception = "misconception"


class LearnerKeyIdeaState(SQLModel, table=True):
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    )
    key_idea_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("keyidea.id", ondelete="CASCADE"), primary_key=True, index=True
        )
    )
    status: KeyIdeaStatus = Field(sa_column=enum_column(KeyIdeaStatus))
    failed_attempts: int = 0


class QuestionLevel(StrEnum):
    explain = "explain"
    apply = "apply"


class QuestionOrigin(StrEnum):
    plan = "plan"
    follow_up = "follow_up"


class QuestionState(StrEnum):
    planned = "planned"
    asked = "asked"
    answered = "answered"


class Question(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    concept_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("concept.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    # Order within the concept for this user; questions are asked in this order.
    position: int
    text: str = Field(sa_column=Column(Text, nullable=False))
    level: QuestionLevel = Field(sa_column=enum_column(QuestionLevel))
    tested_key_idea_ids: list[int] = Field(sa_column=Column(JSON, nullable=False))
    # The passages the question is grounded in; shown as citations after grading.
    source_chunk_ids: list[int] = Field(sa_column=Column(JSON, nullable=False))
    origin: QuestionOrigin = Field(sa_column=enum_column(QuestionOrigin))
    state: QuestionState = Field(sa_column=enum_column(QuestionState))
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class Answer(SQLModel, table=True):
    """Saved before grading; `evaluation` stays empty until grading succeeds."""

    id: int | None = Field(default=None, primary_key=True)
    question_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("question.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        )
    )
    text: str = Field(sa_column=Column(Text, nullable=False))
    # {"key_ideas": [{"id": 21, "status": "correct", "feedback": "..."}]}
    # SQL NULL rather than JSON null while ungraded, so it can be filtered on.
    evaluation: dict | None = Field(default=None, sa_column=Column(JSON(none_as_null=True)))
    dispute_reason: str | None = Field(default=None, sa_column=Column(Text))
    regraded: bool = False
    points_possible: float
    points_earned: float | None = None
    score: float | None = None
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False)
    )
