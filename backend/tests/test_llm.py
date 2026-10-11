from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import claude_agent_sdk
import pytest
from claude_agent_sdk import RateLimitEvent, RateLimitInfo, ResultMessage
from pydantic import BaseModel

from learnpilot import llm
from learnpilot.config import settings


class Answer(BaseModel):
    value: int


def message(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        model="claude-test",
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
        ),
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
    )


@pytest.fixture
def api(monkeypatch):
    """A fake client whose stream returns the queued messages in order."""
    fake = SimpleNamespace(queue=[], requests=[])

    @contextmanager
    def stream(**kwargs):
        fake.requests.append(kwargs)
        yield SimpleNamespace(get_final_message=lambda: fake.queue.pop(0))

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    monkeypatch.setattr(llm, "client", lambda: client)
    return fake


def generate():
    return llm.generate(system="s", user="u", output_type=Answer, effort="high")


def test_returns_parsed_output_with_schema_effort_and_fallback(api):
    api.queue = [message('{"value": 3}')]
    result = generate()
    assert result.output == Answer(value=3)
    assert result.usage == llm.Usage("claude-test", 10, 5)

    request = api.requests[0]
    assert request["output_config"]["effort"] == "high"
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["fallbacks"] == "default"
    assert request["betas"] == [llm.FALLBACK_BETA]


def test_invalid_output_is_retried_once(api):
    api.queue = [message('{"value": "x"}'), message('{"value": 4}')]
    result = generate()
    assert result.output.value == 4
    assert result.usage.input_tokens == 20


def test_invalid_output_twice_fails_with_usage(api):
    api.queue = [message("nope"), message("{}")]
    with pytest.raises(llm.LlmOutputError, match="unusable") as error:
        generate()
    assert error.value.usage.output_tokens == 10


@pytest.mark.parametrize(
    ("stop_reason", "text"), [("refusal", "declined"), ("max_tokens", "ran out of room")]
)
def test_refusal_and_truncation_fail_without_retry(api, stop_reason, text):
    api.queue = [message('{"value": 1', stop_reason)]
    with pytest.raises(llm.LlmOutputError, match=text):
        generate()
    assert len(api.requests) == 1


def test_context_is_cached_before_the_question(api):
    api.queue = [message('{"value": 1}')]
    llm.generate(system="s", user="u", context="c", output_type=Answer, effort="medium")
    [context, user] = api.requests[0]["messages"][0]["content"]
    assert context == {"type": "text", "text": "c", "cache_control": {"type": "ephemeral"}}
    assert user == {"type": "text", "text": "u"}


def test_cached_tokens_count_as_input(api):
    cached = message('{"value": 1}')
    cached.usage.cache_creation_input_tokens = 100
    cached.usage.cache_read_input_tokens = 1000
    api.queue = [cached]
    assert generate().usage.input_tokens == 1110


# Claude subscription (LLM_AUTH=subscription)


def result(output=None, *, is_error=False, text=None, status=None, **usage):
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=2,
        session_id="s",
        stop_reason="end_turn",
        usage={"input_tokens": 10, "output_tokens": 5, **usage},
        result=text,
        structured_output=output,
        model_usage={"claude-test": {}},
        api_error_status=status,
    )


def rate_limit(status: str, resets_at: int | None = None) -> RateLimitEvent:
    return RateLimitEvent(
        rate_limit_info=RateLimitInfo(status=status, resets_at=resets_at), uuid="u", session_id="s"
    )


@pytest.fixture
def subscription(monkeypatch):
    """A fake Claude Code: each query yields the next queued list of messages.

    An exception after the messages stands for the CLI exiting non-zero.
    """
    monkeypatch.setattr(settings, "llm_auth", "subscription")
    monkeypatch.setattr(llm, "_limit_resets_at", None)
    fake = SimpleNamespace(queue=[], options=[], prompts=[])

    async def query(*, prompt, options):
        fake.prompts.append(prompt)
        fake.options.append(options)
        for message in fake.queue.pop(0):
            if isinstance(message, Exception):
                raise message
            yield message

    monkeypatch.setattr(claude_agent_sdk, "query", query)
    return fake


def test_subscription_returns_structured_output_with_options(subscription):
    subscription.queue = [[rate_limit("allowed"), result({"value": 3})]]
    generated = llm.generate(system="s", user="u", context="c", output_type=Answer, effort="high")
    assert generated.output == Answer(value=3)
    assert generated.usage == llm.Usage("claude-test", 10, 5)

    options = subscription.options[0]
    assert (options.system_prompt, options.effort, options.tools) == ("s", "high", [])
    assert (options.setting_sources, options.skills, options.strict_mcp_config) == ([], [], True)
    assert options.env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"
    assert options.output_format["type"] == "json_schema"
    assert options.env["ANTHROPIC_API_KEY"] == ""
    assert subscription.prompts == ["c\n\nu"]


def test_subscription_passes_the_oauth_token(subscription, monkeypatch):
    monkeypatch.setattr(settings, "claude_code_oauth_token", "sk-ant-oat-test")
    subscription.queue = [[result({"value": 1})]]
    generate()
    assert subscription.options[0].env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-test"


def test_subscription_counts_cached_tokens_and_retries_invalid_output(subscription):
    subscription.queue = [
        [result({"value": "x"}, cache_read_input_tokens=100)],
        [result(None, text='{"value": 4}')],
    ]
    generated = generate()
    assert generated.output.value == 4
    assert generated.usage.input_tokens == 120


def test_subscription_limit_reports_the_reset_time(subscription):
    resets_at = int(datetime(2099, 1, 1, 12, tzinfo=UTC).timestamp())
    subscription.queue = [
        [
            rate_limit("rejected", resets_at),
            result(is_error=True, text="Usage limit reached", status=429),
            claude_agent_sdk.ResultError("exit 1"),
        ]
    ]
    with pytest.raises(llm.SubscriptionLimitReachedError) as error:
        generate()
    assert llm.error_message(error.value) == (
        "Your Claude subscription's usage limit is reached. "
        "AI features resume at 2099-01-01 12:00 UTC."
    )
    assert llm.subscription_limit_resets_at() == datetime(2099, 1, 1, 12, tzinfo=UTC)
    assert len(subscription.prompts) == 1

    subscription.queue = [[result({"value": 1})]]
    generate()
    assert llm.subscription_limit_resets_at() is None


def test_subscription_limit_without_reset_time(subscription):
    subscription.queue = [[result(is_error=True, text="API Error: 429", status=429)]]
    with pytest.raises(llm.SubscriptionLimitReachedError) as error:
        generate()
    assert "see claude.ai" in llm.error_message(error.value)


@pytest.mark.parametrize(
    ("text", "status"),
    [("Not logged in · Please run /login", None), ("API Error: 401 token is invalid", 401)],
)
def test_subscription_login_errors(subscription, text, status):
    subscription.queue = [[result(is_error=True, text=text, status=status)]]
    with pytest.raises(llm.SubscriptionAuthError) as error:
        generate()
    assert "CLAUDE_CODE_OAUTH_TOKEN" in llm.error_message(error.value)


def test_subscription_cli_failure(subscription):
    subscription.queue = [[claude_agent_sdk.CLINotFoundError("no claude")]]
    with pytest.raises(llm.SubscriptionError) as error:
        generate()
    assert "not reachable" in llm.error_message(error.value)


def test_subscription_estimates_tokens_locally(subscription):
    assert llm.count_tokens("x" * 10) == 3
