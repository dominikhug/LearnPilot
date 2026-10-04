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
from learnpilot.tutoring import (  # noqa: E402
    FollowUpQuestion,
    GapExplanation,
    Grading,
    QuestionPlan,
)


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

        def __call__(self, *, system, user, output_type, effort, context=None, max_tokens=0):
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
def tutor(monkeypatch, extractor):
    """Replaces the tutoring calls; extraction still goes to `extractor`.

    .plan: None (two questions, each testing every key idea), a function of the key
    idea ids returning a QuestionPlan, or an exception; a list is used up call by call.
    .grade: a status for every tested key idea, a function of the tested ids
    returning a Grading, or an exception; a list is used up call by call.
    .follow_up and .explain: None (a canned result) or an exception.
    """

    class Tutor:
        plan: object = None
        grade: object = "correct"
        follow_up: object = None
        explain: object = None
        calls: list[dict] = []

        def __call__(self, *, system, user, output_type, effort, context=None, max_tokens=0):
            if output_type is Extraction:
                return extractor(system=system, user=user, output_type=output_type, effort=effort)
            self.calls.append(
                {"system": system, "user": user, "context": context, "effort": effort}
            )
            usage = llm.Usage(model="test-model", input_tokens=10, output_tokens=5)
            if output_type is QuestionPlan:
                key_idea_ids = [int(i) for i in re.findall(r'<key_idea id="(\d+)"', context)]
                result = self._next("plan")
                if result is None:
                    result = lambda ids: QuestionPlan.model_validate(  # noqa: E731
                        {
                            "questions": [
                                {
                                    "level": level,
                                    "text": f"{level} it",
                                    "key_idea_ids": ids,
                                    "source_chunk_ids": [],
                                }
                                for level in ("explain", "apply")
                            ]
                        }
                    )
                return self._result(result, key_idea_ids, usage)
            if output_type is FollowUpQuestion:
                targets = re.search(r"Key ideas to test: ([\d, ]+)", user)[1]
                result = self._next("follow_up")
                if result is None:
                    result = lambda _: FollowUpQuestion(  # noqa: E731
                        level="apply", text=f"Follow-up on {targets}", source_chunk_ids=[]
                    )
                return self._result(result, None, usage)
            if output_type is GapExplanation:
                result = self._next("explain")
                if result is None:
                    angle = re.search(r"Angle: (\w+)", user)[1]
                    result = lambda _: GapExplanation(  # noqa: E731
                        text=f"Explained by {angle}.", source_chunk_ids=[999]
                    )
                return self._result(result, None, usage)
            tested = [
                int(i) for i in re.search(r"Key ideas to grade: ([\d, ]+)", user)[1].split(",")
            ]
            result = self._next("grade")
            if isinstance(result, str):
                status = result
                result = lambda ids: Grading.model_validate(  # noqa: E731
                    {
                        "key_ideas": [
                            {"id": i, "status": status, "feedback": f"{status}."} for i in ids
                        ]
                    }
                )
            return self._result(result, tested, usage)

        def _next(self, name):
            value = getattr(self, name)
            if isinstance(value, list):
                return value.pop(0)
            return value

        def _result(self, result, ids, usage):
            if isinstance(result, Exception):
                raise result
            return llm.Generated(result(ids), usage)

    tutor = Tutor()
    tutor.calls = []
    monkeypatch.setattr(llm, "generate", tutor)
    return tutor


@pytest.fixture
def client(engine, token_counter, tutor):
    throttle.reset()
    return TestClient(app)


@pytest.fixture
def logged_in(client):
    assert client.post("/api/auth/login", json={"password": "correct horse"}).status_code == 204
    return client
