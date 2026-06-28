"""
GitHub OAuth flow + session management.

Flow:
1. GET /auth/login          → redirect to GitHub OAuth
2. GET /auth/callback       → GitHub redirects here with ?code=
3. Exchange code for token  → fetch GitHub user
4. Upsert user in DB
5. Link user to any existing installation via account_login match
6. Issue signed session JWT as HttpOnly cookie
7. Redirect to dashboard

GET /auth/me    → return current user from session cookie
GET /auth/logout → clear cookie + redirect
"""

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from db import get_db
from logger import logger
from models.database import Installation, User

router = APIRouter(prefix="/auth", tags=["auth"])

# ── Constants ──────────────────────────────────────────────────────────────────

GITHUB_OAUTH_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_OAUTH_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_API_USER = "https://api.github.com/user"

SESSION_COOKIE_NAME = "fixflow_session"
SESSION_ALGORITHM = "HS256"
SESSION_EXPIRE_DAYS = 30


# ── Session JWT helpers ────────────────────────────────────────────────────────

def _create_session_token(user_id: str, github_login: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "login": github_login,
        "iat": now,
        "exp": now + timedelta(days=SESSION_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.github_client_secret, algorithm=SESSION_ALGORITHM)


def _decode_session_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.github_client_secret, algorithms=[SESSION_ALGORITHM])


# ── Auth dependency — use in any protected endpoint ────────────────────────────

async def require_auth(
    fixflow_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    FastAPI dependency. Validates session cookie and returns the User ORM object.
    Raises 401 if missing or invalid.
    """
    if not fixflow_session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = _decode_session_token(fixflow_session)
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid session")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/login")
async def login(request: Request):
    """
    Redirect to GitHub OAuth authorization page.
    State parameter prevents CSRF.
    """
    settings = get_settings()

    state = secrets.token_urlsafe(32)

    # In production, store state in a short-lived cookie for CSRF validation
    params = {
        "client_id": settings.github_client_id,
        "scope": "read:user user:email",
        "state": state,
        "allow_signup": "true",
    }

    redirect_url = f"{GITHUB_OAUTH_AUTHORIZE}?{urlencode(params)}"

    response = RedirectResponse(url=redirect_url, status_code=302)
    # Store state in cookie to verify on callback
    response.set_cookie(
        key="oauth_state",
        value=state,
        max_age=600,          # 10 minutes — must complete OAuth in time
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
    )
    return response


@router.get("/callback")
async def callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    oauth_state: str | None = Cookie(default=None),
):
    """
    GitHub redirects here after user authorizes.
    Exchange code for token → fetch user → upsert DB → set session cookie.
    """
    settings = get_settings()

    # ── CSRF state check ───────────────────────────────────────────────────────
    if not oauth_state or not secrets.compare_digest(state, oauth_state):
        logger.warning("OAuth state mismatch — possible CSRF attempt")
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    # ── Exchange code for access token ─────────────────────────────────────────
    async with httpx.AsyncClient(timeout=15.0) as client:
        token_response = await client.post(
            GITHUB_OAUTH_TOKEN,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
            },
        )

    if token_response.status_code != 200:
        logger.error(
            "GitHub token exchange failed",
            status=token_response.status_code,
        )
        raise HTTPException(status_code=502, detail="GitHub token exchange failed")

    token_data = token_response.json()
    access_token = token_data.get("access_token")

    if not access_token:
        logger.error("No access_token in GitHub response", data=token_data)
        raise HTTPException(status_code=502, detail="No access token received")

    # ── Fetch GitHub user ──────────────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=15.0) as client:
        user_response = await client.get(
            GITHUB_API_USER,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
        )

    if user_response.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch GitHub user")

    gh_user = user_response.json()
    github_id = gh_user["id"]
    github_login = gh_user["login"]

    # ── Upsert user in DB ──────────────────────────────────────────────────────
    result = await db.execute(
        select(User).where(User.github_id == github_id)
    )
    user = result.scalar_one_or_none()

    if user:
        # Update potentially stale fields
        user.login = github_login
        user.name = gh_user.get("name")
        user.avatar_url = gh_user.get("avatar_url")
        logger.info("Existing user logged in", login=github_login)
    else:
        user = User(
            github_id=github_id,
            login=github_login,
            name=gh_user.get("name"),
            avatar_url=gh_user.get("avatar_url"),
        )
        db.add(user)
        await db.flush()
        logger.info("New user created", login=github_login)

    # ── Session bridge: link installations to this user ────────────────────────
    # When the App was installed before the user OAuth'd, installation.user_id
    # is null. Match on account_login to back-fill the link.
    inst_result = await db.execute(
        select(Installation).where(
            Installation.account_login == github_login,
            Installation.user_id == None,  # noqa: E711 — SQLAlchemy IS NULL
        )
    )
    unlinked = inst_result.scalars().all()

    for installation in unlinked:
        installation.user_id = user.id

    if unlinked:
        logger.info(
            "Session bridge: linked installations to user",
            login=github_login,
            count=len(unlinked),
        )

    await db.commit()

    # ── Issue session cookie ───────────────────────────────────────────────────
    session_token = _create_session_token(str(user.id), github_login)

    frontend_url = (
        "https://fixflow.vercel.app/dashboard"
        if settings.is_production
        else "http://localhost:3000/dashboard"
    )

    response = RedirectResponse(url=frontend_url, status_code=302)

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=60 * 60 * 24 * SESSION_EXPIRE_DAYS,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )
    # Clear the OAuth state cookie
    response.delete_cookie("oauth_state")

    return response


@router.get("/me")
async def get_me(current_user: User = Depends(require_auth)):
    """Return current authenticated user's profile."""
    return {
        "id": str(current_user.id),
        "github_id": current_user.github_id,
        "login": current_user.login,
        "name": current_user.name,
        "avatar_url": current_user.avatar_url,
        "created_at": current_user.created_at.isoformat(),
    }


@router.get("/logout")
async def logout():
    """Clear session cookie and redirect to home."""
    settings = get_settings()
    frontend_url = (
        "https://fixflow.vercel.app"
        if settings.is_production
        else "http://localhost:3000"
    )
    response = RedirectResponse(url=frontend_url, status_code=302)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response