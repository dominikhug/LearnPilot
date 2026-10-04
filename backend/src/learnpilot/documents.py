from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from learnpilot.auth import CurrentUser
from learnpilot.config import settings
from learnpilot.db import get_session
from learnpilot.ingestion import (
    SUPPORTED_SUFFIXES,
    IngestionError,
    extract_chunks,
    title_from_filename,
)
from learnpilot.models import (
    Chunk,
    Concept,
    ConceptStatus,
    Document,
    DocumentStatus,
    LearnerConceptState,
    ProcessingStep,
)
from learnpilot.processing import can_retry, process_document

# Generous upper bound on characters per token: text longer than this many
# characters per allowed token is certainly over the limit, so it is rejected
# without a round trip to the token-counting API.
MAX_CHARS_PER_TOKEN = 10

router = APIRouter(prefix="/api/documents", tags=["documents"])
DbSession = Annotated[Session, Depends(get_session)]


class DocumentOut(BaseModel):
    id: int
    title: str
    status: DocumentStatus
    step: ProcessingStep
    token_count: int | None
    language: str | None
    error_message: str | None
    can_retry: bool
    concept_count: int
    mastered_count: int
    created_at: datetime

    @classmethod
    def of(
        cls, document: Document, concept_count: int = 0, mastered_count: int = 0
    ) -> "DocumentOut":
        # Attribute access (unlike model_dump) reloads an instance expired by commit.
        stored = cls.model_fields.keys() - {"can_retry", "concept_count", "mastered_count"}
        fields = {name: getattr(document, name) for name in stored}
        return cls(
            **fields,
            can_retry=can_retry(document),
            concept_count=concept_count,
            mastered_count=mastered_count,
        )


class LimitsOut(BaseModel):
    max_upload_mb: int
    max_document_tokens: int
    file_types: list[str]


class ChunkOut(BaseModel):
    id: int
    position: int
    page: int | None
    section: str | None
    text: str


def owned_document(session: Session, user: CurrentUser, document_id: int) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


@router.get("")
def list_documents(user: CurrentUser, session: DbSession) -> list[DocumentOut]:
    concept_count = (
        select(func.count())
        .where(Concept.document_id == Document.id)
        .correlate(Document)
        .scalar_subquery()
    )
    mastered_count = (
        select(func.count())
        .select_from(LearnerConceptState)
        .join(Concept, col(Concept.id) == LearnerConceptState.concept_id)
        .where(
            Concept.document_id == Document.id,
            LearnerConceptState.user_id == user.id,
            LearnerConceptState.status == ConceptStatus.mastered,
        )
        .correlate(Document)
        .scalar_subquery()
    )
    rows = session.exec(
        select(Document, concept_count, mastered_count)
        .where(Document.user_id == user.id)
        .order_by(col(Document.created_at).desc(), col(Document.id).desc())
    )
    return [DocumentOut.of(d, count, mastered) for d, count, mastered in rows]


@router.get("/limits")
def limits(user: CurrentUser) -> LimitsOut:
    """Lets the frontend reject a file before uploading it."""
    return LimitsOut(
        max_upload_mb=settings.max_upload_mb,
        max_document_tokens=settings.max_document_tokens,
        file_types=sorted(SUPPORTED_SUFFIXES),
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def upload_document(
    file: UploadFile, user: CurrentUser, session: DbSession, background: BackgroundTasks
) -> DocumentOut:
    # Checked before the file is parsed. Starlette has already spooled it to disk.
    if file.size is not None and file.size > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"The file is larger than {settings.max_upload_mb} MB.",
        )
    filename = file.filename or ""
    try:
        drafts = extract_chunks(filename, file.file.read())
    except IngestionError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e

    if sum(len(d.text) for d in drafts) > settings.max_document_tokens * MAX_CHARS_PER_TOKEN:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"This document is far above the limit of {settings.max_document_tokens:,} tokens. "
            "Split it into smaller parts and upload them separately.",
        )

    document = Document(
        user_id=user.id,
        title=title_from_filename(filename),
        status=DocumentStatus.processing,
        step=ProcessingStep.counting_tokens,
    )
    session.add(document)
    session.flush()
    session.add_all(
        Chunk(document_id=document.id, position=i, page=d.page, section=d.section, text=d.text)
        for i, d in enumerate(drafts)
    )
    session.commit()
    background.add_task(process_document, document.id)
    return DocumentOut.of(document)


@router.get("/{document_id}")
def get_document(document_id: int, user: CurrentUser, session: DbSession) -> DocumentOut:
    document = owned_document(session, user, document_id)
    count = session.exec(
        select(func.count()).select_from(Concept).where(Concept.document_id == document_id)
    ).one()
    return DocumentOut.of(document, count)


@router.get("/{document_id}/chunks")
def list_chunks(document_id: int, user: CurrentUser, session: DbSession) -> list[ChunkOut]:
    owned_document(session, user, document_id)
    chunks = session.exec(
        select(Chunk).where(Chunk.document_id == document_id).order_by(col(Chunk.position))
    )
    return [ChunkOut.model_validate(c, from_attributes=True) for c in chunks]


@router.post("/{document_id}/retry")
def retry_document(
    document_id: int, user: CurrentUser, session: DbSession, background: BackgroundTasks
) -> DocumentOut:
    """Reruns processing on the stored chunks; no new upload needed."""
    document = owned_document(session, user, document_id)
    if not can_retry(document):
        raise HTTPException(status.HTTP_409_CONFLICT, "This document cannot be retried.")
    document.status = DocumentStatus.processing
    document.step = ProcessingStep.counting_tokens
    document.error_message = None
    session.commit()
    background.add_task(process_document, document_id)
    return DocumentOut.of(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: int, user: CurrentUser, session: DbSession) -> Response:
    # Chunks (and later everything else belonging to it) go via ON DELETE CASCADE.
    session.delete(owned_document(session, user, document_id))
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
