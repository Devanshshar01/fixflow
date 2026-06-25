"""
GitHub API client for FixFlow.

Responsibilities:
- JWT generation (proves we are the App)
- Installation token fetching with 5-min expiry buffer cache
- Log ZIP download with redirect following
- PR comment posting and editing via the fixflow:managed marker
"""

import time
import zipfile
import io
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field

import httpx
from jose import jwt

from config import get_settings
from logger import logger


# ── Token cache ───────────────────────────────────────────────────────────────

@dataclass
class _CachedToken:
    token: str
    expires_at: datetime


_token_cache: dict[int, _CachedToken] = {}

# ── Constants ─────────────────────────────────────────────────────────────────

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
JWT_EXPIRY_SECONDS = 540          # 9 min — GitHub max is 10, leave buffer
TOKEN_EXPIRY_BUFFER = timedelta(minutes=5)
REQUEST_TIMEOUT = 30.0            # seconds
LOG_DOWNLOAD_TIMEOUT = 60.0       # ZIP downloads can be slow


# ── Internal helpers ──────────────────────────────────────────────────────────

def _make_jwt() -> str:
    """
    Generate a short-lived JWT signed with the App's private key.
    This proves to GitHub that we are the App itself.
    """
    settings = get_settings()
    now = int(time.time())

    payload = {
        "iat": now - 60,          # issued-at: 60s in past to account for clock skew
        "exp": now + JWT_EXPIRY_SECONDS,
        "iss": settings.github_app_id,
    }

    token = jwt.encode(
        payload,
        settings.github_private_key,
        algorithm="RS256",
    )

    logger.debug("Generated App JWT", expires_in_seconds=JWT_EXPIRY_SECONDS)
    return token


def _api_headers(token: str, is_jwt: bool = False) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "FixFlow-App/1.0",
    }


# ── Public API ────────────────────────────────────────────────────────────────

async def get_installation_token(installation_id: int) -> str:
    """
    Return a valid installation access token for the given installation.
    Caches tokens and only refreshes within 5 minutes of expiry.
    """
    now = datetime.now(timezone.utc)

    # Check cache first
    cached = _token_cache.get(installation_id)
    if cached and (cached.expires_at - TOKEN_EXPIRY_BUFFER) > now:
        logger.debug(
            "Using cached installation token",
            installation_id=installation_id,
            expires_at=cached.expires_at.isoformat(),
        )
        return cached.token

    # Fetch a fresh token
    app_jwt = _make_jwt()
    url = f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(url, headers=_api_headers(app_jwt, is_jwt=True))

    if response.status_code != 201:
        logger.error(
            "Failed to fetch installation token",
            installation_id=installation_id,
            status=response.status_code,
            body=response.text[:500],
        )
        raise GitHubAPIError(
            f"Installation token fetch failed: {response.status_code}",
            status_code=response.status_code,
        )

    data = response.json()
    token = data["token"]

    # Parse expiry from GitHub's response (e.g. "2024-01-01T12:00:00Z")
    expires_at = datetime.fromisoformat(
        data["expires_at"].replace("Z", "+00:00")
    )

    _token_cache[installation_id] = _CachedToken(token=token, expires_at=expires_at)

    logger.info(
        "Fetched fresh installation token",
        installation_id=installation_id,
        expires_at=expires_at.isoformat(),
    )
    return token


async def download_logs_zip(
    installation_id: int,
    owner: str,
    repo: str,
    run_id: int,
) -> bytes:
    """
    Download the log ZIP for a workflow run.
    GitHub returns a 302 redirect to a short-lived S3 URL.
    httpx follows redirects automatically.
    """
    token = await get_installation_token(installation_id)
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/actions/runs/{run_id}/logs"

    logger.info(
        "Downloading workflow logs",
        owner=owner,
        repo=repo,
        run_id=run_id,
    )

    async with httpx.AsyncClient(
        timeout=LOG_DOWNLOAD_TIMEOUT,
        follow_redirects=True,
    ) as client:
        response = await client.get(url, headers=_api_headers(token))

    if response.status_code == 404:
        raise LogsNotFoundError(
            f"Logs not found for run {run_id} — may have expired (GitHub keeps logs 90 days)"
        )

    if response.status_code != 200:
        raise GitHubAPIError(
            f"Log download failed: {response.status_code}",
            status_code=response.status_code,
        )

    logger.info(
        "Log ZIP downloaded",
        run_id=run_id,
        size_bytes=len(response.content),
    )
    return response.content


async def post_pr_comment(
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    body: str,
) -> int:
    """
    Post a new comment on a PR. Returns the comment ID.
    """
    token = await get_installation_token(installation_id)
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{pr_number}/comments"

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(
            url,
            headers=_api_headers(token),
            json={"body": body},
        )

    if response.status_code != 201:
        logger.error(
            "Failed to post PR comment",
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            status=response.status_code,
            body=response.text[:500],
        )
        raise GitHubAPIError(
            f"PR comment post failed: {response.status_code}",
            status_code=response.status_code,
        )

    comment_id = response.json()["id"]
    logger.info(
        "PR comment posted",
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        comment_id=comment_id,
    )
    return comment_id


async def update_pr_comment(
    installation_id: int,
    owner: str,
    repo: str,
    comment_id: int,
    body: str,
) -> None:
    """
    Edit an existing PR comment by ID.
    Used to replace the placeholder with the real analysis.
    """
    token = await get_installation_token(installation_id)
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/comments/{comment_id}"

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.patch(
            url,
            headers=_api_headers(token),
            json={"body": body},
        )

    if response.status_code != 200:
        raise GitHubAPIError(
            f"PR comment update failed: {response.status_code}",
            status_code=response.status_code,
        )

    logger.info(
        "PR comment updated",
        owner=owner,
        repo=repo,
        comment_id=comment_id,
    )


async def find_existing_fixflow_comment(
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
) -> int | None:
    """
    Search existing PR comments for one containing the fixflow:managed marker.
    Returns the comment ID if found, None otherwise.
    This prevents duplicate comments on webhook retries.
    """
    token = await get_installation_token(installation_id)
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{pr_number}/comments"

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(
            url,
            headers=_api_headers(token),
            params={"per_page": 100},
        )

    if response.status_code != 200:
        return None

    for comment in response.json():
        if "<!-- fixflow:managed -->" in comment.get("body", ""):
            return comment["id"]

    return None


async def get_repository_info(
    installation_id: int,
    owner: str,
    repo: str,
) -> dict:
    """Fetch basic repo metadata."""
    token = await get_installation_token(installation_id)
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(url, headers=_api_headers(token))

    if response.status_code != 200:
        raise GitHubAPIError(
            f"Repo info fetch failed: {response.status_code}",
            status_code=response.status_code,
        )

    return response.json()


# ── Exceptions ────────────────────────────────────────────────────────────────

class GitHubAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class LogsNotFoundError(Exception):
    pass