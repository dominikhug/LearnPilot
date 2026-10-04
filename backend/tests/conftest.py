import os
import re

# Must be set before learnpilot.config is imported; environment beats .env.
os.environ.update(
    APP_PASSWORD="correct horse",
    SESSION_SECRET="test-secret",
    SESSION_COOKIE_SECURE="false",
    # Tests never call the real API, even when .env holds a key.
    ANTHROPIC_API_KEY="",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from learnpilot import db, llm  # noqa: E402
from learnpilot.auth import throttle  # noqa: E402
from learnpilot.extraction import Extraction  # noqa: E402
from learnpilot.main import app  # noqa: E402
from learnpilot.models import DEFAULT_USER_ID, User  # noqa: E402


@pytest.fixture
def engine(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        # SQLite ignores ON DELETE CASCADE unless this is on, unlike PostgreSQL.
        connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id=DEFAULT_USER_ID, name="default"))
        session.commit()
    # Requests and background tasks both open sessions on db.engine.
    monkeypatch.setattr(db, "engine", engine)
    return engine


@pytest.fixture
def token_counter(monkeypatch):
    """Replaces the token-counting API call; set .result to an int or an exception."""

    class Counter:
        result: int | Exception = 1234
        calls: list[str] = []

        def __call__(self, text: str) -> int:
            self.calls.append(text)
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    counter = Counter()
    counter.calls = []
    monkeypatch.setattr(llm, "count_tokens", counter)
    return counter


def sample_extraction(chunk_ids: list[int]) -> Extraction:
    first = chunk_ids[0]
    return Extraction.model_validate(
        {
            "language": "en",
            "concepts": [
                {
                    "id": 1,
                    "name": "Function",
                    "definition": "A named block of code.",
                    "key_ideas": ["Has parameters", "Returns a value"],
                    "source_chunk_ids": [first],
                },
                {
                    "id": 2,
                    "name": "Recursion",
                    "definition": "A function calling itself.",
                    "key_ideas": ["Calls itself", "Needs a base case", "Base case is reachable"],
                    "source_chunk_ids": [first],
                },
            ],
            "prerequisites": [{"prerequisite_id": 1, "concept_id": 2, "confidence": 0.9}],
        }
    )


@pytest.fixture
def extractor(monkeypatch):
    """Replaces the extraction LLM call.

    Set .result to an Extraction, a function of the prompt's chunk ids, or an exception.
    """

    class Extractor:
        result: object = staticmethod(sample_extraction)
        calls: list[str] = []

        def __call__(self, *, system, user, output_type, effort, max_tokens=0):
            self.calls.append(user)
            usage = llm.Usage(model="test-model", input_tokens=100, output_tokens=50)
            if isinstance(self.result, Exception):
                raise self.result
            if callable(self.result):
                ids = [int(i) for i in re.findall(r'<chunk id="(\d+)"', user)]
                return llm.Generated(self.result(ids), usage)
            return llm.Generated(self.result, usage)

    extractor = Extractor()
    extractor.calls = []
    monkeypatch.setattr(llm, "generate", extractor)
    return extractor


@pytest.fixture
def client(engine, token_counter, extractor):
    throttle.reset()
    return TestClient(app)


@pytest.fixture
def logged_in(client):
    assert client.post("/api/auth/login", json={"password": "correct horse"}).status_code == 204
    return client
