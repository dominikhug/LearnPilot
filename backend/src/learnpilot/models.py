from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer
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
