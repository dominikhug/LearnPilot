import os

# Must be set before learnpilot.config is imported; environment beats .env.
os.environ.update(
    APP_PASSWORD="correct horse",
    SESSION_SECRET="test-secret",
    SESSION_COOKIE_SECURE="false",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from learnpilot.auth import throttle  # noqa: E402
from learnpilot.db import get_session  # noqa: E402
from learnpilot.main import app  # noqa: E402
from learnpilot.models import DEFAULT_USER_ID, User  # noqa: E402


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id=DEFAULT_USER_ID, name="default"))
        session.commit()

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    throttle.reset()
    yield TestClient(app)
    app.dependency_overrides.clear()
