"""Background processing of an uploaded document. Runs inside the server process."""

import logging

import anthropic
from sqlmodel import Session, col, select, update

from learnpilot import llm
from learnpilot.config import settings
from learnpilot.db import new_session
from learnpilot.models import Chunk, Document, DocumentStatus, ProcessingStep

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


def process_document(document_id: int) -> None:
    with new_session() as session:
        document = session.get(Document, document_id)
        if document is None or document.status != DocumentStatus.processing:
            return
        document.step = ProcessingStep.counting_tokens
        session.commit()

        try:
            tokens = llm.count_tokens(_document_text(session, document_id))
        except anthropic.AuthenticationError:
            _fail(
                session,
                document_id,
                "The AI service rejected the API key. Check the server configuration.",
            )
            return
        except anthropic.APIError:
            log.exception("Token counting failed for document %s", document_id)
            _fail(
                session,
                document_id,
                "The AI service is not reachable right now. Try again in a few minutes.",
            )
            return
        except llm.LlmNotConfiguredError:
            _fail(
                session,
                document_id,
                "The AI service is not configured. Check ANTHROPIC_API_KEY on the server.",
            )
            return
        except Exception:
            log.exception("Processing failed for document %s", document_id)
            _fail(session, document_id, "Processing failed unexpectedly. Try again.")
            return

        # Re-read: the document may have been deleted while the request ran.
        document = session.get(Document, document_id, populate_existing=True)
        if document is None:
            return
        document.token_count = tokens
        if tokens > settings.max_document_tokens:
            document.status = DocumentStatus.failed
            document.error_message = too_large_message(tokens)
        else:
            document.status = DocumentStatus.ready
            document.step = ProcessingStep.done
            document.error_message = None
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


def _document_text(session: Session, document_id: int) -> str:
    texts = session.exec(
        select(Chunk.text).where(Chunk.document_id == document_id).order_by(col(Chunk.position))
    )
    return "\n\n".join(texts)


def _fail(session: Session, document_id: int, message: str) -> None:
    session.rollback()
    document = session.get(Document, document_id, populate_existing=True)
    if document is not None:
        document.status = DocumentStatus.failed
        document.error_message = message
        session.commit()
