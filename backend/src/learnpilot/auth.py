import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlmodel import Session

from learnpilot.config import settings
from learnpilot.db import get_session
from learnpilot.models import DEFAULT_USER_ID, User

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60


class LoginThrottle:
    """Global in-memory throttle: after 5 failed logins, refuse all logins for 1 minute.

    Global rather than per IP because there is a single account and client IPs
    behind a proxy are spoofable. Kept in memory; sufficient with one instance.
    """

    def __init__(self) -> None:
        self.failures = 0
        self.locked_until = 0.0

    def retry_after(self) -> int:
        return max(0, int(self.locked_until - time.monotonic() + 0.999))

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= MAX_FAILED_ATTEMPTS:
            self.failures = 0
            self.locked_until = time.monotonic() + LOCKOUT_SECONDS

    def reset(self) -> None:
        self.failures = 0
        self.locked_until = 0.0


throttle = LoginThrottle()
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


class UserOut(BaseModel):
    id: int
    name: str


def current_user(request: Request, session: Annotated[Session, Depends(get_session)]) -> User:
    """The single place that decides who the user is; real auth replaces only this."""
    user_id = request.session.get("user_id")
    user = session.get(User, user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
def login(body: LoginRequest, request: Request) -> Response:
    wait = throttle.retry_after()
    if wait > 0:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many failed attempts. Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )
    expected = settings.app_password.encode()
    # An unset APP_PASSWORD never matches, so a misconfigured deployment stays closed.
    if not expected or not secrets.compare_digest(body.password.encode(), expected):
        throttle.record_failure()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong password")
    throttle.reset()
    request.session.clear()
    request.session["user_id"] = DEFAULT_USER_ID
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> Response:
    request.session.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    return UserOut(id=user.id, name=user.name)
