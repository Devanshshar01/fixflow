"""
Analytics router — summary stats for the dashboard and for manual verification.

GET /analytics/summary  — top-level numbers
GET /analytics/patterns — rule engine hit counts, sorted by usage
GET /analytics/recent   — last 20 analyzed runs
"""

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from db import get_db
from logger import logger
from models.database import WorkflowRun, FailureAnalysis, ErrorPattern, Repository

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary")
async def get_summary(db: AsyncSession = Depends(get_db)):
    """
    Top-level numbers. Used by the dashboard stats cards.
    """

    # Total runs analyzed
    total_result = await db.execute(
        select(func.count()).select_from(WorkflowRun).where(
            WorkflowRun.status == "completed"
        )
    )
    total_analyzed = total_result.scalar_one() or 0

    # Runs by source (rule_engine vs AI vs degraded)
    source_result = await db.execute(
        select(FailureAnalysis.source, func.count().label("count"))
        .group_by(FailureAnalysis.source)
    )
    by_source = {row.source: row.count for row in source_result.fetchall()}

    # Runs by category
    category_result = await db.execute(
        select(FailureAnalysis.error_category, func.count().label("count"))
        .group_by(FailureAnalysis.error_category)
        .order_by(desc("count"))
        .limit(10)
    )
    by_category = [
        {"category": row.error_category, "count": row.count}
        for row in category_result.fetchall()
    ]

    # Average analysis time (ms) — only completed runs
    avg_ms_result = await db.execute(
        select(func.avg(WorkflowRun.analysis_ms)).where(
            WorkflowRun.status == "completed",
            WorkflowRun.analysis_ms.isnot(None),
        )
    )
    avg_ms = avg_ms_result.scalar_one()
    avg_ms = round(avg_ms) if avg_ms else None

    # Rule engine resolution rate
    rule_count = by_source.get("rule_engine", 0)
    rule_rate = round((rule_count / total_analyzed) * 100) if total_analyzed > 0 else 0

    # Pending / stuck runs
    stuck_result = await db.execute(
        select(func.count()).select_from(WorkflowRun).where(
            WorkflowRun.status.in_(["pending", "analyzing"])
        )
    )
    stuck_count = stuck_result.scalar_one() or 0

    # Runs in last 24 hours
    yesterday = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_result = await db.execute(
        select(func.count()).select_from(WorkflowRun).where(
            WorkflowRun.triggered_at >= yesterday
        )
    )
    recent_count = recent_result.scalar_one() or 0

    return {
        "total_analyzed": total_analyzed,
        "last_24h": recent_count,
        "avg_analysis_ms": avg_ms,
        "rule_engine_rate_pct": rule_rate,
        "stuck_runs": stuck_count,
        "by_source": by_source,
        "by_category": by_category,
    }


@router.get("/patterns")
async def get_pattern_stats(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """
    Rule engine patterns sorted by hit count.
    Useful for understanding which errors are most common.
    """
    result = await db.execute(
        select(ErrorPattern)
        .where(ErrorPattern.is_active == True)
        .order_by(desc(ErrorPattern.hit_count))
        .limit(limit)
    )
    patterns = result.scalars().all()

    return {
        "patterns": [
            {
                "rule_id": p.pattern_id,
                "category": p.category,
                "severity": p.severity,
                "hit_count": p.hit_count,
                "success_rate": p.success_rate,
                "root_cause_template": p.root_cause,
                "fix_url": p.fix_url,
            }
            for p in patterns
        ]
    }


@router.get("/recent")
async def get_recent_runs(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """
    Most recent analyzed workflow runs with their analysis results.
    """
    result = await db.execute(
        select(WorkflowRun, FailureAnalysis, Repository)
        .join(FailureAnalysis, FailureAnalysis.run_id == WorkflowRun.id, isouter=True)
        .join(Repository, Repository.id == WorkflowRun.repository_id, isouter=True)
        .where(WorkflowRun.status == "completed")
        .order_by(desc(WorkflowRun.analyzed_at))
        .limit(limit)
    )
    rows = result.fetchall()

    return {
        "runs": [
            {
                "run_id": str(run.id),
                "github_run_id": run.github_run_id,
                "repo": repo.full_name if repo else None,
                "workflow": run.workflow_name,
                "pr_number": run.pr_number,
                "analysis_ms": run.analysis_ms,
                "analyzed_at": run.analyzed_at.isoformat() if run.analyzed_at else None,
                "comment_posted": run.comment_posted,
                "analysis": {
                    "category": analysis.error_category,
                    "source": analysis.source,
                    "confidence": analysis.confidence,
                    "failed_step": analysis.failed_step,
                    "redaction_count": analysis.redaction_count,
                    "root_cause": analysis.root_cause,
                    "fix": analysis.fix_suggestion,
                } if analysis else None,
            }
            for run, analysis, repo in rows
        ]
    }