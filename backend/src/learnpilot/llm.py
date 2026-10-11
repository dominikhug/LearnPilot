import asyncio
import logging
import math
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from functools import cache
from typing import Literal

import anthropic
import claude_agent_sdk
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, col, func, select

from learnpilot.config import settings
from learnpilot.models import Document, LlmCall, LlmPurpose, utcnow

log = logging.getLogger(__name__)

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# A declined request is rerun server-side on the model Anthropic recommends
# for the refusal category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LlmNotConfiguredError(Exception):
    pass


class DailyLimitReachedError(Exception):
    """DAILY_TOKEN_LIMIT is used up; AI features pause until the next day (UTC)."""


class SubscriptionError(Exception):
    """A call on the Claude subscription (LLM_AUTH=subscription) failed."""


class SubscriptionAuthError(SubscriptionError):
    """No Claude login, or the OAuth token was rejected."""


class SubscriptionLimitReachedError(SubscriptionError):
    """The subscription's 5-hour or weekly limit is used up."""

    def __init__(self, resets_at: datetime | None) -> None:
        super().__init__(f"Subscription limit reached, resets at {resets_at}")
        self.resets_at = resets_at


# When the last call hit the subscription limit: shown on the usage page until it passes.
# Kept in memory only, so it is forgotten on restart.
_limit_resets_at: datetime | None = None


def subscription_limit_resets_at() -> datetime | None:
    """When the subscription limit resets, if the last call hit it and it has not yet."""
    if _limit_resets_at is not None and _limit_resets_at > utcnow():
        return _limit_resets_at
    return None


@cache
def client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise LlmNotConfiguredError("ANTHROPIC_API_KEY is not set")
    # The SDK retries rate limits, overload and connection errors with backoff;
    # max_retries=2 gives the 3 attempts the concept asks for.
    return anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=2)


def count_tokens(text: str) -> int:
    if settings.llm_auth == "subscription":
        # The subscription has no token-counting endpoint; about 4 characters per token
        # is close enough for the document size check.
        return math.ceil(len(text) / 4)
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
    Runs on the API key or the Claude subscription, as LLM_AUTH says.
    """
    if settings.llm_auth == "subscription":
        return _generate_subscription(system, user, output_type, effort, context)
    return _generate_api(system, user, output_type, effort, context, max_tokens)


def _generate_api[T: BaseModel](
    system: str,
    user: str,
    output_type: type[T],
    effort: Effort,
    context: str | None,
    max_tokens: int,
) -> Generated[T]:
    """Streams, so a large output limit does not run into HTTP timeouts."""
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


def _generate_subscription[T: BaseModel](
    system: str, user: str, output_type: type[T], effort: Effort, context: str | None
) -> Generated[T]:
    """Runs the call through Claude Code (the Agent SDK) on the user's Claude subscription.

    Claude Code caches the prompt itself, so `context` simply goes first.
    """
    usage = Usage(model=settings.llm_model)
    prompt = user if context is None else f"{context}\n\n{user}"
    options = claude_agent_sdk.ClaudeAgentOptions(
        system_prompt=system,
        model=settings.llm_model,
        effort=effort,
        output_format={"type": "json_schema", "schema": anthropic.transform_schema(output_type)},
        # A plain model call: no tools, skills or MCP servers (not even the claude.ai
        # connectors of the login, which document text could otherwise steer), and no
        # settings or CLAUDE.md from the machine.
        tools=[],
        skills=[],
        strict_mcp_config=True,
        setting_sources=[],
        # Structured output is returned through a tool call, which takes a turn of its own.
        max_turns=3,
        env=_subscription_env(),
    )
    for attempt in (1, 2):
        with tempfile.TemporaryDirectory() as cwd:
            options.cwd = cwd
            result = asyncio.run(_query(prompt, options))

        usage.input_tokens += (
            result.usage.get("input_tokens", 0)
            + result.usage.get("cache_creation_input_tokens", 0)
            + result.usage.get("cache_read_input_tokens", 0)
        )
        usage.output_tokens += result.usage.get("output_tokens", 0)
        if result.model:
            usage.model = result.model
        if result.stop_reason == "refusal":
            raise LlmOutputError("The AI service declined this request.", usage)
        if result.stop_reason == "max_tokens":
            raise LlmOutputError("The AI service ran out of room for its answer.", usage)
        try:
            if result.output is not None:
                return Generated(output_type.model_validate(result.output), usage)
            return Generated(output_type.model_validate_json(result.text or ""), usage)
        except ValidationError as e:
            log.warning("Invalid structured output (attempt %s): %s", attempt, e)

    raise LlmOutputError("The AI service returned an unusable answer. Try again.", usage)


def _subscription_env() -> dict[str, str]:
    # An API key in the environment would win over the subscription login.
    env = {"ANTHROPIC_API_KEY": "", "ENABLE_CLAUDEAI_MCP_SERVERS": "false"}
    # The token may come from .env, which only reaches settings, not the CLI's environment.
    if settings.claude_code_oauth_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = settings.claude_code_oauth_token
    return env


@dataclass
class _Result:
    output: object
    text: str | None
    usage: dict
    model: str | None
    stop_reason: str | None


async def _query(prompt: str, options: claude_agent_sdk.ClaudeAgentOptions) -> _Result:
    """Runs one query and returns its result, or raises a SubscriptionError."""
    global _limit_resets_at
    rejected: claude_agent_sdk.RateLimitInfo | None = None
    result: claude_agent_sdk.ResultMessage | None = None
    try:
        async for message in claude_agent_sdk.query(prompt=prompt, options=options):
            if isinstance(message, claude_agent_sdk.RateLimitEvent):
                if message.rate_limit_info.status == "rejected":
                    rejected = message.rate_limit_info
            elif isinstance(message, claude_agent_sdk.ResultMessage):
                result = message
    except claude_agent_sdk.ClaudeSDKError as e:
        # The CLI exits non-zero after an error result; that result says more.
        if result is None or not result.is_error:
            raise SubscriptionError(str(e)) from e

    if result is None:
        raise SubscriptionError("Claude Code ended without a result")
    if result.is_error:
        text = " ".join([result.result or "", *(result.errors or [])])
        if result.api_error_status in (401, 403) or "Not logged in" in text:
            raise SubscriptionAuthError(text)
        if result.api_error_status == 429 or rejected is not None:
            resets_at = rejected.resets_at if rejected is not None else None
            _limit_resets_at = (
                datetime.fromtimestamp(resets_at, UTC) if resets_at is not None else None
            )
            raise SubscriptionLimitReachedError(_limit_resets_at)
        raise SubscriptionError(text)

    _limit_resets_at = None
    return _Result(
        output=result.structured_output,
        text=result.result,
        usage=result.usage or {},
        # Claude Code may make small side calls on another model; prefer the one asked for.
        model=settings.llm_model
        if settings.llm_model in (result.model_usage or {})
        else next(iter(result.model_usage or {}), None),
        stop_reason=result.stop_reason,
    )


def error_message(error: Exception) -> str | None:
    """A message for the user when `error` comes from calling the AI service."""
    match error:
        case DailyLimitReachedError():
            return (
                f"The daily AI limit of {settings.daily_token_limit:,} tokens is used up. "
                "AI features resume at midnight UTC."
            )
        case SubscriptionLimitReachedError(resets_at=resets_at):
            when = (
                f"at {resets_at:%Y-%m-%d %H:%M} UTC"
                if resets_at is not None
                else "when it resets (see claude.ai → Settings → Usage)"
            )
            return f"Your Claude subscription's usage limit is reached. AI features resume {when}."
        case SubscriptionAuthError():
            return (
                "The Claude login is missing or expired. Run `claude setup-token` and set "
                "CLAUDE_CODE_OAUTH_TOKEN on the server."
            )
        case SubscriptionError():
            return "The AI service is not reachable right now. Try again in a few minutes."
        case LlmNotConfiguredError():
            return "The AI service is not configured. Check ANTHROPIC_API_KEY on the server."
        case anthropic.AuthenticationError():
            return "The AI service rejected the API key. Check the server configuration."
        case anthropic.RateLimitError():
            return "The AI service's rate limit is reached. Try again in a minute."
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


def day_start(now: datetime | None = None) -> datetime:
    """Midnight UTC of the current day; the daily limit counts from here."""
    now = now or utcnow()
    return datetime.combine(now.date(), time(), tzinfo=now.tzinfo)


def next_day_start(now: datetime | None = None) -> datetime:
    return day_start(now) + timedelta(days=1)


def tokens_used_today(session: Session) -> int:
    """Input and output tokens of all users since midnight UTC.

    The limit caps what the API key costs, so it is shared by all users.
    """
    return session.exec(
        select(func.coalesce(func.sum(LlmCall.input_tokens + LlmCall.output_tokens), 0)).where(
            col(LlmCall.created_at) >= day_start()
        )
    ).one()


def check_daily_limit(session: Session) -> None:
    """Raises before a call once the limit is reached.

    Checked before, not during, a call: the last call of a day may go over the limit.
    Only for the API key: the subscription has limits of its own and costs nothing extra.
    """
    if settings.llm_auth == "subscription":
        return
    if tokens_used_today(session) >= settings.daily_token_limit:
        raise DailyLimitReachedError()
