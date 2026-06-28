"""
Repositories router — returns repos and their failure stats for the dashboard.
All endpoints require authentication.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from db import get_db
from logger import logger
from models.database import Installation, Repository, WorkflowRun, FailureAnalysis, User
from routers.auth import require_auth

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.get("")
async def list_repositories(
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Return all repositories across all installations for the current user.
    Includes failure counts and last failure timestamp.
    """
    # Get all installations for this user
    inst_result = await db.execute(
        select(Installation).where(
            Installation.user_id == current_user.id,
            Installation.suspended_at == None,  # noqa: E711
        )
    )
    installations = inst_result.scalars().all()

    if not installations:
        return {"repositories": [], "installations": []}

    installation_ids = [i.id for i in installations]

    # Get all active repos for these installations
    repo_result = await db.execute(
        select(Repository).where(
            Repository.installation_id.in_(installation_ids),
            Repository.is_active == True,  # noqa: E712
        ).order_by(Repository.full_name)
    )
    repos = repo_result.scalars().all()

    if not repos:
        return {
            "repositories": [],
            "installations": [
                {
                    "id": str(i.id),
                    "account_login": i.account_login,
                    "account_type": i.account_type,
                }
                for i in installations
            ],
        }

    repo_ids = [r.id for r in repos]

    # Failure counts per repo
    counts_result = await db.execute(
        select(
            WorkflowRun.repository_id,
            func.count().label("total_failures"),
            func.max(WorkflowRun.triggered_at).label("last_failure_at"),
        )
        .where(
            WorkflowRun.repository_id.in_(repo_ids),
            WorkflowRun.status == "completed",
        )
        .group_by(WorkflowRun.repository_id)
    )
    counts_map = {
        row.repository_id: {
            "total_failures": row.total_failures,
            "last_failure_at": row.last_failure_at,
        }
        for row in counts_result.fetchall()
    }

    return {
        "repositories": [
            {
                "id": str(r.id),
                "full_name": r.full_name,
                "default_branch": r.default_branch,
                "is_active": r.is_active,
                "added_at": r.added_at.isoformat(),
                "total_failures": counts_map.get(r.id, {}).get("total_failures", 0),
                "last_failure_at": (
                    counts_map.get(r.id, {}).get("last_failure_at").isoformat()
                    if counts_map.get(r.id, {}).get("last_failure_at")
                    else None
                ),
            }
            for r in repos
        ],
        "installations": [
            {
                "id": str(i.id),
                "account_login": i.account_login,
                "account_type": i.account_type,
            }
            for i in installations
        ],
    }


@router.get("/{repo_id}/failures")
async def get_repo_failures(
    repo_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Return paginated failure history for a specific repository.
    Verifies the repo belongs to the current user before returning data.
    """
    # Verify ownership — user must own the installation that owns this repo
    repo_result = await db.execute(
        select(Repository)
        .join(Installation, Installation.id == Repository.installation_id)
        .where(
            Repository.id == repo_id,
            Installation.user_id == current_user.id,
        )
    )
    repo = repo_result.scalar_one_or_none()

    if not repo:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Repository not found")

    # Paginated failures with analysis data
    runs_result = await db.execute(
        select(WorkflowRun, FailureAnalysis)
        .join(
            FailureAnalysis,
            FailureAnalysis.run_id == WorkflowRun.id,
            isouter=True,
        )
        .where(
            WorkflowRun.repository_id == repo.id,
            WorkflowRun.status == "completed",
        )
        .order_by(desc(WorkflowRun.triggered_at))
        .limit(limit)
        .offset(offset)
    )
    rows = runs_result.fetchall()

    # Total count for pagination
    count_result = await db.execute(
        select(func.count()).select_from(WorkflowRun).where(
            WorkflowRun.repository_id == repo.id,
            WorkflowRun.status == "completed",
        )
    )
    total = count_result.scalar_one() or 0

    return {
        "repository": {
            "id": str(repo.id),
            "full_name": repo.full_name,
            "default_branch": repo.default_branch,
        },
        "total": total,
        "limit": limit,
        "offset": offset,
        "failures": [
            {
                "id": str(run.id),
                "github_run_id": run.github_run_id,
                "workflow_name": run.workflow_name,
                "head_sha": run.head_sha,
                "pr_number": run.pr_number,
                "triggered_at": run.triggered_at.isoformat(),
                "analyzed_at": run.analyzed_at.isoformat() if run.analyzed_at else None,
                "analysis_ms": run.analysis_ms,
                "comment_posted": run.comment_posted,
                "analysis": {
                    "category": analysis.error_category,
                    "source": analysis.source,
                    "confidence": analysis.confidence,
                    "failed_step": analysis.failed_step,
                    "root_cause": analysis.root_cause,
                    "fix": analysis.fix_suggestion,
                    "redaction_count": analysis.redaction_count,
                } if analysis else None,
            }
            for run, analysis in rows
        ],
    }