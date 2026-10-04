import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, text
from starlette.middleware.sessions import SessionMiddleware

from learnpilot import auth, concepts, documents, learning
from learnpilot.config import settings
from learnpilot.db import get_session, new_session
from learnpilot.processing import recover_interrupted_documents

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    with new_session() as session:
        if count := recover_interrupted_documents(session):
            log.warning("Marked %s interrupted document(s) as failed", count)
    yield


app = FastAPI(
    title="LearnPilot", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="learnpilot_session",
    max_age=settings.session_max_age_days * 24 * 3600,
    same_site="lax",
    https_only=settings.session_cookie_secure,
)
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(concepts.router)
app.include_router(learning.router)


@app.get("/api/health")
def health(session: Annotated[Session, Depends(get_session)]) -> dict[str, str]:
    session.exec(text("SELECT 1"))
    return {"status": "ok"}


# The built Vue app is served from the same origin (no CORS). Unknown non-API
# paths return index.html so client-side routes survive a page reload.
if settings.static_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=settings.static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        file = (settings.static_dir / path).resolve()
        if path and file.is_file() and file.is_relative_to(settings.static_dir.resolve()):
            return FileResponse(file)
        return FileResponse(settings.static_dir / "index.html")
