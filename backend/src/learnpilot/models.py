from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, Column, DateTime, Enum, Float, ForeignKey, Integer
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
