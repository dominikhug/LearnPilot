"""Background processing of an uploaded document. Runs inside the server process."""

import logging

import anthropic
from sqlmodel import Session, col, update

from learnpilot import extraction, llm
from learnpilot.config import settings
from learnpilot.db import new_session
from learnpilot.models import (
    Document,
    DocumentStatus,
    LlmCall,
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
        except anthropic.AuthenticationError:
            _fail(
                session,
                document_id,
                "The AI service rejected the API key. Check the server configuration.",
            )
        except anthropic.APIError:
            log.exception("AI service call failed for document %s", document_id)
            _fail(
                session,
                document_id,
                "The AI service is not reachable right now. Try again in a few minutes.",
            )
        except llm.LlmNotConfiguredError:
            _fail(
                session,
                document_id,
                "The AI service is not configured. Check ANTHROPIC_API_KEY on the server.",
            )
        except (llm.LlmOutputError, extraction.ExtractionError) as e:
            _fail(session, document_id, f"Finding concepts failed. {e}")
        except Exception:
            log.exception("Processing failed for document %s", document_id)
            _fail(session, document_id, "Processing failed unexpectedly. Try again.")


def _run(session: Session, document_id: int) -> None:
    document = _set_step(session, document_id, ProcessingStep.counting_tokens)
    chunks = extraction.document_chunks(session, document_id)
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
        _log_call(session, user_id, document_id, e.usage)
        raise
    _log_call(session, user_id, document_id, result.usage)

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


def _log_call(session: Session, user_id: int, document_id: int, usage: llm.Usage) -> None:
    # Logged even when the document is gone, so the tokens still count; the
    # foreign key would reject a deleted document's id.
    session.rollback()
    exists = session.get(Document, document_id, populate_existing=True) is not None
    session.add(
        LlmCall(
            user_id=user_id,
            document_id=document_id if exists else None,
            purpose=LlmPurpose.concept_extraction,
            model=usage.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
    )
    session.commit()


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
