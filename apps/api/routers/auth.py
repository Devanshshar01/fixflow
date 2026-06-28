"""
GitHub OAuth flow + session management.

Cross-domain fix: since the API (onrender.com) and frontend (vercel.app)
are on different domains, browsers block cross-domain cookies entirely.
Solution: after OAuth completes, redirect to the frontend with the session
token as a URL parameter. The frontend stores it in localStorage and sends
it as an Authorization header on every API request.
"""

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from db import get_db
from logger import logger
from models.database import Installation, User

router = APIRouter(prefix="/auth", tags=["auth"])

GITHUB_OAUTH_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_OAUTH_TOKEN     = "https://github.com/login/oauth/access_token"
GITHUB_API_USER        = "https://api.github.com/user"

SESSION_COOKIE_NAME   = "fixflow_session"
SESSION_ALGORITHM     = "HS256"
SESSION_EXPIRE_DAYS   = 30


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
    return jwt.encode(
        payload, settings.github_client_secret, algorithm=SESSION_ALGORITHM
    )


def _decode_session_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(
        token, settings.github_client_secret, algorithms=[SESSION_ALGORITHM]
    )


# ── Auth dependency ────────────────────────────────────────────────────────────

async def require_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Accepts the session token from either:
    - Authorization: Bearer <token>  header  (cross-domain frontend)
    - fixflow_session cookie                  (same-domain / future)
    """
    token: str | None = None

    # 1. Authorization header (primary — used by cross-domain Vercel frontend)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]

    # 2. Cookie fallback (same-domain deployments)
    if not token:
        token = request.cookies.get(SESSION_COOKIE_NAME)

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = _decode_session_token(token)
        user_id: str | None = payload.get("sub")
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
async def login():
    """Redirect to GitHub OAuth."""
    settings = get_settings()
    state = secrets.token_urlsafe(32)

    params = {
        "client_id": settings.github_client_id,
        "scope": "read:user user:email",
        "state": state,
        "allow_signup": "true",
    }

    response = RedirectResponse(
        url=f"{GITHUB_OAUTH_AUTHORIZE}?{urlencode(params)}",
        status_code=302,
    )
    response.set_cookie(
        key="oauth_state",
        value=state,
        max_age=600,
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
):
    """
    GitHub redirects here after user authorizes.
    Redirects to frontend with token in URL parameter so the frontend
    can store it and use it as a Bearer token on API calls.
    """
    settings = get_settings()

    # ── CSRF state check ───────────────────────────────────────────────────────
    oauth_state = request.cookies.get("oauth_state")
    if not oauth_state or not secrets.compare_digest(state, oauth_state):
        logger.warning("OAuth state mismatch — possible CSRF")
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    # ── Exchange code for GitHub access token ──────────────────────────────────
    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            GITHUB_OAUTH_TOKEN,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
            },
        )

    if token_resp.status_code != 200:
        logger.error("GitHub token exchange failed", status=token_resp.status_code)
        raise HTTPException(status_code=502, detail="GitHub token exchange failed")

    access_token = token_resp.json().get("access_token")
    if not access_token:
        logger.error("No access_token in GitHub response")
        raise HTTPException(status_code=502, detail="No access token received")

    # ── Fetch GitHub user ──────────────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=15.0) as client:
        user_resp = await client.get(
            GITHUB_API_USER,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
        )

    if user_resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch GitHub user")

    gh_user      = user_resp.json()
    github_id    = gh_user["id"]
    github_login = gh_user["login"]

    # ── Upsert user ────────────────────────────────────────────────────────────
    result = await db.execute(select(User).where(User.github_id == github_id))
    user = result.scalar_one_or_none()

    if user:
        user.login      = github_login
        user.name       = gh_user.get("name")
        user.avatar_url = gh_user.get("avatar_url")
        logger.info("Existing user logged in", login=github_login)
    else:
        user = User(
            github_id   = github_id,
            login       = github_login,
            name        = gh_user.get("name"),
            avatar_url  = gh_user.get("avatar_url"),
        )
        db.add(user)
        await db.flush()
        logger.info("New user created", login=github_login)

    # ── Session bridge: link orphaned installations ────────────────────────────
    inst_result = await db.execute(
        select(Installation).where(
            Installation.account_login == github_login,
            Installation.user_id == None,  # noqa: E711
        )
    )
    unlinked = inst_result.scalars().all()
    for inst in unlinked:
        inst.user_id = user.id

    if unlinked:
        logger.info(
            "Session bridge: linked installations",
            login=github_login,
            count=len(unlinked),
        )

    await db.commit()

    # ── Issue session token and redirect to frontend ───────────────────────────
    session_token = _create_session_token(str(user.id), github_login)

    # Pass token as URL param — frontend stores in localStorage
    # and sends as Authorization: Bearer <token> header
    redirect_url = f"https://fixflow-henna-alpha.vercel.app/auth/callback?token={session_token}"

    response = RedirectResponse(url=redirect_url, status_code=302)
    response.delete_cookie("oauth_state")
    return response


@router.get("/me")
async def get_me(current_user: User = Depends(require_auth)):
    """Return current authenticated user."""
    return {
        "id":         str(current_user.id),
        "github_id":  current_user.github_id,
        "login":      current_user.login,
        "name":       current_user.name,
        "avatar_url": current_user.avatar_url,
        "created_at": current_user.created_at.isoformat(),
    }


@router.get("/logout")
async def logout():
    """Clear session and redirect to home."""
    settings = get_settings()
    response = RedirectResponse(url=settings.frontend_url, status_code=302)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response