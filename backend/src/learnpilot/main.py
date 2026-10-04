from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, text
from starlette.middleware.sessions import SessionMiddleware

from learnpilot import auth
from learnpilot.config import settings
from learnpilot.db import get_session

app = FastAPI(title="LearnPilot", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="learnpilot_session",
    max_age=settings.session_max_age_days * 24 * 3600,
    same_site="lax",
    https_only=settings.session_cookie_secure,
)
app.include_router(auth.router)


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
