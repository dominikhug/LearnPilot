import logging
from dataclasses import dataclass
from functools import cache
from typing import Literal

import anthropic
from pydantic import BaseModel, ValidationError
from sqlmodel import Session

from learnpilot.config import settings
from learnpilot.models import Document, LlmCall, LlmPurpose

log = logging.getLogger(__name__)

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# A declined request is rerun server-side on the model Anthropic recommends
# for the refusal category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LlmNotConfiguredError(Exception):
    pass


@cache
def client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise LlmNotConfiguredError("ANTHROPIC_API_KEY is not set")
    # The SDK retries rate limits, overload and connection errors with backoff;
    # max_retries=2 gives the 3 attempts the concept asks for.
    return anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=2)


def count_tokens(text: str) -> int:
    response = client().messages.count_tokens(
        model=settings.llm_model,
        messages=[{"role": "user", "content": text}],
    )
    return response.input_tokens


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Generated[T: BaseModel]:
    output: T
    usage: Usage


class LlmOutputError(Exception):
    """The model returned no usable result; the message is shown to the user.

    Carries the tokens spent, so failed calls are still logged.
    """

    def __init__(self, message: str, usage: Usage) -> None:
        super().__init__(message)
        self.usage = usage


def generate[T: BaseModel](
    *,
    system: str,
    user: str,
    output_type: type[T],
    effort: Effort,
    context: str | None = None,
    max_tokens: int = 64_000,
) -> Generated[T]:
    """One structured-output call. Invalid output is retried once.

    `context` goes before `user` and is cached together with the system prompt, so
    calls that share both (e.g. gradings of one concept) reuse it.
    Streams, so a large output limit does not run into HTTP timeouts.
    """
    usage = Usage(model=settings.llm_model)
    output_format = {"type": "json_schema", "schema": anthropic.transform_schema(output_type)}
    content: list[dict] = []
    if context is not None:
        content.append({"type": "text", "text": context, "cache_control": {"type": "ephemeral"}})
    content.append({"type": "text", "text": user})
    for attempt in (1, 2):
        with client().beta.messages.stream(
            model=settings.llm_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": effort, "format": output_format},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            message = stream.get_final_message()

        usage.model = message.model
        # Cached tokens count too, so the daily limit sees every token sent.
        usage.input_tokens += (
            message.usage.input_tokens
            + (message.usage.cache_creation_input_tokens or 0)
            + (message.usage.cache_read_input_tokens or 0)
        )
        usage.output_tokens += message.usage.output_tokens
        # Checked before parsing: a refused or truncated answer is not worth a retry.
        if message.stop_reason == "refusal":
            raise LlmOutputError("The AI service declined this request.", usage)
        if message.stop_reason == "max_tokens":
            raise LlmOutputError("The AI service ran out of room for its answer.", usage)
        text = "".join(block.text for block in message.content if block.type == "text")
        try:
            return Generated(output_type.model_validate_json(text), usage)
        except ValidationError as e:
            log.warning("Invalid structured output (attempt %s): %s", attempt, e)

    raise LlmOutputError("The AI service returned an unusable answer. Try again.", usage)


def error_message(error: Exception) -> str | None:
    """A message for the user when `error` comes from calling the AI service."""
    match error:
        case LlmNotConfiguredError():
            return "The AI service is not configured. Check ANTHROPIC_API_KEY on the server."
        case anthropic.AuthenticationError():
            return "The AI service rejected the API key. Check the server configuration."
        case anthropic.APIError():
            return "The AI service is not reachable right now. Try again in a few minutes."
        case LlmOutputError():
            return str(error)
    return None


def log_call(
    session: Session, user_id: int, document_id: int, purpose: LlmPurpose, usage: Usage
) -> None:
    """Logs the call in its own transaction; uncommitted changes are discarded.

    Logged even when the document is gone, so the tokens still count; the
    foreign key would reject a deleted document's id.
    """
    session.rollback()
    exists = session.get(Document, document_id, populate_existing=True) is not None
    session.add(
        LlmCall(
            user_id=user_id,
            document_id=document_id if exists else None,
            purpose=purpose,
            model=usage.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
    )
    session.commit()
