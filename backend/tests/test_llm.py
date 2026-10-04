from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from learnpilot import llm


class Answer(BaseModel):
    value: int


def message(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        model="claude-test",
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
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
