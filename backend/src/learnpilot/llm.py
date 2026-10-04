from functools import cache

import anthropic

from learnpilot.config import settings


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
