"""AI usage from the LlmCall log: today's tokens and limits, totals per document and purpose."""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import col, func, select

from learnpilot import llm
from learnpilot.auth import CurrentUser
from learnpilot.config import settings
from learnpilot.documents import DbSession
from learnpilot.models import Document, LlmCall, LlmPurpose

router = APIRouter(prefix="/api/usage", tags=["usage"])


class TokensOut(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int


class DocumentUsageOut(TokensOut):
    # None: calls of documents deleted since.
    document_id: int | None
    title: str | None


class PurposeUsageOut(TokensOut):
    purpose: LlmPurpose


class UsageOut(BaseModel):
    # All users' tokens today; this is what the daily limit counts.
    used_today: int
    # None: calls run on the Claude subscription, which has no daily limit here.
    daily_limit: int | None
    resets_at: datetime
    paused: bool
    # Set while the subscription's own usage limit is reached; None if the reset time is unknown.
    subscription_limited: bool
    subscription_resets_at: datetime | None
    # The current user's calls, all time.
    total: TokensOut
    by_document: list[DocumentUsageOut]
    by_purpose: list[PurposeUsageOut]


_SUMS = (
    func.count(),
    func.coalesce(func.sum(LlmCall.input_tokens), 0),
    func.coalesce(func.sum(LlmCall.output_tokens), 0),
)


def _tokens(calls: int, input_tokens: int, output_tokens: int) -> dict:
    return {"calls": calls, "input_tokens": input_tokens, "output_tokens": output_tokens}


@router.get("")
def get_usage(user: CurrentUser, session: DbSession) -> UsageOut:
    mine = LlmCall.user_id == user.id
    used_today = llm.tokens_used_today(session)
    total = session.exec(select(*_SUMS).where(mine)).one()
    by_document = session.exec(
        select(LlmCall.document_id, Document.title, *_SUMS)
        .join(Document, col(Document.id) == LlmCall.document_id, isouter=True)
        .where(mine)
        .group_by(col(LlmCall.document_id), col(Document.title))
    ).all()
    by_purpose = session.exec(
        select(LlmCall.purpose, *_SUMS).where(mine).group_by(col(LlmCall.purpose))
    ).all()

    def size(row) -> int:
        return row[-2] + row[-1]

    daily_limited = settings.llm_auth == "api_key"
    subscription_resets_at = llm.subscription_limit_resets_at()
    return UsageOut(
        used_today=used_today,
        daily_limit=settings.daily_token_limit if daily_limited else None,
        resets_at=llm.next_day_start(),
        paused=daily_limited and used_today >= settings.daily_token_limit,
        subscription_limited=subscription_resets_at is not None,
        subscription_resets_at=subscription_resets_at,
        total=TokensOut(**_tokens(*total)),
        by_document=[
            DocumentUsageOut(document_id=document_id, title=title, **_tokens(*sums))
            for document_id, title, *sums in sorted(by_document, key=size, reverse=True)
        ],
        by_purpose=[
            PurposeUsageOut(purpose=purpose, **_tokens(*sums))
            for purpose, *sums in sorted(by_purpose, key=size, reverse=True)
        ],
    )
