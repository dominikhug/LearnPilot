import os

# Must be set before learnpilot.config is imported; environment beats .env.
os.environ.update(
    APP_PASSWORD="correct horse",
    SESSION_SECRET="test-secret",
    SESSION_COOKIE_SECURE="false",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from learnpilot import db, llm  # noqa: E402
from learnpilot.auth import throttle  # noqa: E402
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


@pytest.fixture
def client(engine, token_counter):
    throttle.reset()
    return TestClient(app)


@pytest.fixture
def logged_in(client):
    assert client.post("/api/auth/login", json={"password": "correct horse"}).status_code == 204
    return client
