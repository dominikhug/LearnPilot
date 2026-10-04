from collections.abc import Iterator

from sqlmodel import Session, create_engine

from learnpilot.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)


def new_session() -> Session:
    """A session outside a request, e.g. for background tasks. Tests replace `engine`."""
    return Session(engine)


def get_session() -> Iterator[Session]:
    with new_session() as session:
        yield session
