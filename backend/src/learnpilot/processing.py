"""Background processing of an uploaded document. Runs inside the server process."""

import logging

from sqlmodel import Session, col, update

from learnpilot import extraction, llm
from learnpilot.config import settings
from learnpilot.db import new_session
from learnpilot.models import (
    Document,
    DocumentStatus,
    LlmPurpose,
    ProcessingStep,
)

log = logging.getLogger(__name__)

INTERRUPTED_MESSAGE = "Processing was interrupted. Try again."


def too_large_message(tokens: int) -> str:
    return (
        f"This document has {tokens:,} tokens; the limit is {settings.max_document_tokens:,}. "
        "Split it into smaller parts and upload them separately."
    )


def can_retry(document: Document) -> bool:
    """Retrying cannot help a document that is over the token limit."""
    return document.status == DocumentStatus.failed and (
        document.token_count is None or document.token_count <= settings.max_document_tokens
    )


class _Gone(Exception):
    """The document was deleted while processing ran."""


def process_document(document_id: int) -> None:
    with new_session() as session:
        document = session.get(Document, document_id)
        if document is None or document.status != DocumentStatus.processing:
            return
        try:
            _run(session, document_id)
        except _Gone:
            session.rollback()
        except (llm.LlmOutputError, extraction.ExtractionError) as e:
            _fail(session, document_id, f"Finding concepts failed. {e}")
        except llm.DailyLimitReachedError as e:
            _fail(session, document_id, f"{llm.error_message(e)} Retry then.")
        except Exception as e:
            log.exception("Processing failed for document %s", document_id)
            message = llm.error_message(e) or "Processing failed unexpectedly. Try again."
            _fail(session, document_id, message)


def _run(session: Session, document_id: int) -> None:
    document = _set_step(session, document_id, ProcessingStep.counting_tokens)
    chunks = extraction.document_chunks(session, document_id)
    llm.check_daily_limit(session)
    tokens = llm.count_tokens("\n\n".join(c.text for c in chunks))

    document = _reload(session, document_id)
    document.token_count = tokens
    if tokens > settings.max_document_tokens:
        document.status = DocumentStatus.failed
        document.error_message = too_large_message(tokens)
        session.commit()
        return

    document = _set_step(session, document_id, ProcessingStep.extracting_concepts)
    user_id, title = document.user_id, document.title
    try:
        result = llm.generate(
            system=extraction.SYSTEM_PROMPT,
            user=extraction.build_prompt(title, chunks),
            output_type=extraction.Extraction,
            effort="high",
        )
    except llm.LlmOutputError as e:
        llm.log_call(session, user_id, document_id, LlmPurpose.concept_extraction, e.usage)
        raise
    llm.log_call(session, user_id, document_id, LlmPurpose.concept_extraction, result.usage)

    _set_step(session, document_id, ProcessingStep.building_graph)
    draft = extraction.validate(result.output, {c.id for c in chunks})
    document = _reload(session, document_id)
    extraction.save(session, document_id, draft)
    document.language = draft.language
    document.status = DocumentStatus.ready
    document.step = ProcessingStep.done
    document.error_message = None
    session.commit()


def _reload(session: Session, document_id: int) -> Document:
    # Re-read: the document may have been deleted while a request ran.
    document = session.get(Document, document_id, populate_existing=True)
    if document is None:
        raise _Gone
    return document


def _set_step(session: Session, document_id: int, step: ProcessingStep) -> Document:
    document = _reload(session, document_id)
    document.step = step
    session.commit()
    return document


def recover_interrupted_documents(session: Session) -> int:
    """On server start nothing can still be running, so 'processing' means interrupted."""
    result = session.exec(
        update(Document)
        .where(col(Document.status) == DocumentStatus.processing)
        .values(status=DocumentStatus.failed, error_message=INTERRUPTED_MESSAGE)
    )
    session.commit()
    return result.rowcount


def _fail(session: Session, document_id: int, message: str) -> None:
    session.rollback()
    document = session.get(Document, document_id, populate_existing=True)
    if document is not None:
        document.status = DocumentStatus.failed
        document.error_message = message
        session.commit()
