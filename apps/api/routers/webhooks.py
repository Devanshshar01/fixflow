"""
Webhook router — the core of FixFlow.

Flow per failed workflow_run:
1. Verify HMAC-SHA256 signature
2. Return 200 immediately (GitHub will retry on non-200 or timeout >10s)
3. Background task:
   a. Idempotency check
   b. Upsert repository record
   c. Create WorkflowRun with status=pending
   d. Download + parse logs
   e. Redact secrets
   f. Rule engine → if match, skip AI
   g. AI fallback if no rule match
   h. Format + post PR comment
   i. Update WorkflowRun status=completed
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from config import get_settings
from db import get_db, AsyncSessionLocal
from logger import logger
from models.database import Installation, Repository, WorkflowRun, FailureAnalysis
from services import github as gh
from services.log_parser import parse_log_zip
from services.redactor import redact
from services.rule_engine import match as rule_match
from services.ai_analyzer import get_analyzer, AnalysisContext, AIAnalysisError

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# ── Signature verification ────────────────────────────────────────────────────

def verify_webhook_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ── Comment formatter ─────────────────────────────────────────────────────────

def _format_pr_comment(
    *,
    root_cause: str,
    fix: str,
    prevention: str | None,
    category: str,
    source: str,
    confidence: int | None,
    failed_step: str | None,
    cascading_steps: list[str],
    redaction_count: int,
    analysis_ms: int,
    ecosystem: str,
) -> str:
    """Build the full markdown body for the PR comment."""

    # Confidence badge
    if confidence is None or source == "rule_engine":
        confidence_line = "🟢 **Matched known pattern** — deterministic fix"
    elif confidence >= 75:
        confidence_line = f"🟢 **High confidence** ({confidence}/100)"
    elif confidence >= 40:
        confidence_line = f"🟡 **Moderate confidence** ({confidence}/100)"
    else:
        confidence_line = f"🔴 **Low confidence** ({confidence}/100) — treat as a starting point"

    cascading_note = ""
    if cascading_steps:
        steps_str = ", ".join(f"`{s}`" for s in cascading_steps[:3])
        more = f" and {len(cascading_steps) - 3} more" if len(cascading_steps) > 3 else ""
        cascading_note = f"\n> ℹ️ **Cascading failures** also detected in: {steps_str}{more} — fixing the root step above should resolve these.\n"

    redaction_note = (
        f"\n> 🔒 **{redaction_count} secret{'s' if redaction_count != 1 else ''} redacted** before analysis\n"
        if redaction_count > 0
        else ""
    )

    prevention_section = (
        f"\n**Prevention**\n{prevention}\n"
        if prevention
        else ""
    )

    source_label = {
        "rule_engine": "Rule engine (deterministic)",
        "gemini": "Gemini AI",
        "ollama": "Ollama (local AI)",
    }.get(source, source)

    return f"""<!-- fixflow:managed -->
## 🔍 FixFlow Analysis

**Failed step:** `{failed_step or "unknown"}`
**Ecosystem:** `{ecosystem}` | **Category:** `{category}`
{confidence_line}
{cascading_note}
---

**Root cause**
{root_cause}

**Fix**
{fix}
{prevention_section}{redaction_note}
---

<sub>Analyzed by [FixFlow](https://github.com/fixflow) · {source_label} · {analysis_ms}ms</sub>
"""


def _format_degraded_comment(
    *,
    failed_step: str | None,
    redacted_snippet: str,
    reason: str,
    redaction_count: int,
) -> str:
    """Fallback comment when AI fails or confidence is too low."""
    redaction_note = (
        f"\n> 🔒 **{redaction_count} secret{'s' if redaction_count != 1 else ''} redacted** before display\n"
        if redaction_count > 0
        else ""
    )

    # Truncate snippet for display
    lines = redacted_snippet.splitlines()
    display = "\n".join(lines[:40])
    truncated = len(lines) > 40

    return f"""<!-- fixflow:managed -->
## 🔍 FixFlow Analysis

**Failed step:** `{failed_step or "unknown"}`

> ⚠️ {reason}
{redaction_note}
**Relevant log snippet:**
{display}{"..." if truncated else ""}
---

<sub>Analyzed by [FixFlow](https://github.com/fixflow) · degraded mode</sub>
"""


# ── Background analysis pipeline ──────────────────────────────────────────────

async def _run_analysis_pipeline(
    payload: dict,
    installation_id: int,
) -> None:
    """
    Full analysis pipeline. Runs as a background task.
    Opens its own DB session — BackgroundTasks run after the request is closed.
    """
    start_time = time.monotonic()
    run_data = payload.get("workflow_run", {})
    repo_data = payload.get("repository", {})
    github_run_id = run_data.get("id")
    owner, repo_name = repo_data.get("full_name", "/").split("/", 1)

    with logger.contextualize(
        github_run_id=github_run_id,
        installation_id=installation_id,
        repo=repo_data.get("full_name"),
    ):
        async with AsyncSessionLocal() as db:
            try:
                await _pipeline(
                    db=db,
                    payload=payload,
                    run_data=run_data,
                    repo_data=repo_data,
                    github_run_id=github_run_id,
                    owner=owner,
                    repo_name=repo_name,
                    installation_id=installation_id,
                    start_time=start_time,
                )
                await db.commit()

            except Exception as exc:
                await db.rollback()
                logger.error(
                    "Analysis pipeline failed",
                    error=str(exc),
                    exc_info=True,
                )
                # Mark run as failed in DB so it can be retried
                await _mark_run_failed(db, github_run_id, str(exc))


async def _pipeline(
    *,
    db: AsyncSession,
    payload: dict,
    run_data: dict,
    repo_data: dict,
    github_run_id: int,
    owner: str,
    repo_name: str,
    installation_id: int,
    start_time: float,
) -> None:

    # ── Step 1: Idempotency check ──────────────────────────────────────────────
    existing = await db.execute(
        select(WorkflowRun).where(WorkflowRun.github_run_id == github_run_id)
    )
    if existing.scalar_one_or_none():
        logger.info("Skipping — run already processed")
        return

    # ── Step 2: Resolve installation record ────────────────────────────────────
    installation_row = await db.execute(
        select(Installation).where(
            Installation.installation_id == installation_id
        )
    )
    installation_obj = installation_row.scalar_one_or_none()

    # ── Step 3: Upsert repository ──────────────────────────────────────────────
    repo_row = await db.execute(
        select(Repository).where(
            Repository.github_repo_id == repo_data.get("id")
        )
    )
    repo_obj = repo_row.scalar_one_or_none()

    if not repo_obj:
        repo_obj = Repository(
            installation_id=installation_obj.id if installation_obj else None,
            github_repo_id=repo_data["id"],
            full_name=repo_data["full_name"],
            default_branch=repo_data.get("default_branch", "main"),
        )
        db.add(repo_obj)
        await db.flush()   # Get the ID without committing
        logger.info("New repository registered", repo=repo_data["full_name"])

    # ── Step 4: Create WorkflowRun record (status=pending) ────────────────────
    pr_list = run_data.get("pull_requests", [])
    pr_number = pr_list[0]["number"] if pr_list else None

    workflow_run = WorkflowRun(
        repository_id=repo_obj.id,
        github_run_id=github_run_id,
        workflow_name=run_data.get("name"),
        head_sha=run_data.get("head_sha"),
        pr_number=pr_number,
        conclusion=run_data.get("conclusion", "failure"),
        triggered_at=datetime.now(timezone.utc),
        status="analyzing",
    )
    db.add(workflow_run)
    await db.flush()

    logger.info(
        "WorkflowRun created",
        run_id=str(workflow_run.id),
        pr_number=pr_number,
        workflow=run_data.get("name"),
    )

    # ── Step 5: Post placeholder comment immediately ───────────────────────────
    placeholder_comment_id: int | None = None
    if pr_number:
        # Check for existing fixflow comment first (idempotency on comments)
        existing_comment_id = await gh.find_existing_fixflow_comment(
            installation_id, owner, repo_name, pr_number
        )

        if existing_comment_id:
            placeholder_comment_id = existing_comment_id
            logger.info("Reusing existing FixFlow comment", comment_id=existing_comment_id)
        else:
            placeholder_body = (
                "<!-- fixflow:managed -->\n"
                "## 🔍 FixFlow Analysis\n\n"
                "⏳ Analyzing CI failure — fix suggestion will appear here shortly...\n\n"
                f"**Workflow:** `{run_data.get('name', 'unknown')}`\n"
                f"**Commit:** `{(run_data.get('head_sha') or '')[:7]}`"
            )
            try:
                placeholder_comment_id = await gh.post_pr_comment(
                    installation_id, owner, repo_name, pr_number, placeholder_body
                )
            except gh.GitHubAPIError as exc:
                logger.warning("Could not post placeholder comment", error=str(exc))

    # ── Step 6: Download and parse logs ───────────────────────────────────────
    try:
        zip_bytes = await gh.download_logs_zip(
            installation_id, owner, repo_name, github_run_id
        )
    except gh.LogsNotFoundError:
        logger.warning("Logs not found — may have expired")
        await _mark_run_failed(db, github_run_id, "logs_expired")
        return
    except gh.GitHubAPIError as exc:
        logger.error("Log download failed", error=str(exc))
        await _mark_run_failed(db, github_run_id, f"log_download_failed: {exc}")
        return

    parsed = parse_log_zip(zip_bytes)

    # ── Step 7: Redact secrets ─────────────────────────────────────────────────
    redaction_result = redact(parsed.snippet)
    clean_snippet = redaction_result.text
    redaction_count = redaction_result.count

    logger.info(
        "Redaction complete",
        count=redaction_count,
        categories=redaction_result.categories,
    )

    # ── Step 8: Rule engine ────────────────────────────────────────────────────
    rule_result = rule_match(clean_snippet, parsed.ecosystem)

    analysis_source = "unknown"
    root_cause = "Unable to determine root cause"
    fix = "Review the log snippet above for details"
    prevention = None
    category = "unknown"
    confidence: int | None = None
    rule_id: str | None = None

    if rule_result:
        analysis_source = "rule_engine"
        root_cause = rule_result.root_cause
        fix = rule_result.fix
        prevention = rule_result.prevention
        category = rule_result.category
        confidence = 100
        rule_id = rule_result.rule_id

        logger.info(
            "Rule engine resolved failure",
            rule_id=rule_id,
            category=category,
        )

    else:
        # ── Step 9: AI fallback ────────────────────────────────────────────────
        logger.info("No rule match — falling back to AI")
        try:
            analyzer = get_analyzer()
            ai_result = await analyzer.analyze(
                AnalysisContext(
                    workflow_name=run_data.get("name", "unknown"),
                    failed_step=parsed.root_step.name if parsed.root_step else "unknown",
                    ecosystem=parsed.ecosystem,
                    redacted_snippet=clean_snippet,
                )
            )
            analysis_source = get_settings().ai_provider
            root_cause = ai_result.root_cause
            fix = ai_result.fix
            prevention = ai_result.prevention
            category = ai_result.category
            confidence = ai_result.confidence

            logger.info(
                "AI analysis complete",
                source=analysis_source,
                confidence=confidence,
                category=category,
            )

        except AIAnalysisError as exc:
            logger.error("AI analysis failed", error=str(exc))
            analysis_source = "degraded"

    # ── Step 10: Build and post/update PR comment ──────────────────────────────
    analysis_ms = round((time.monotonic() - start_time) * 1000)

    if analysis_source == "degraded":
        comment_body = _format_degraded_comment(
            failed_step=parsed.root_step.name if parsed.root_step else None,
            redacted_snippet=clean_snippet,
            reason="Rule engine had no match and AI analysis failed. Showing redacted log snippet.",
            redaction_count=redaction_count,
        )
    else:
        comment_body = _format_pr_comment(
            root_cause=root_cause,
            fix=fix,
            prevention=prevention,
            category=category,
            source=analysis_source,
            confidence=confidence,
            failed_step=parsed.root_step.name if parsed.root_step else None,
            cascading_steps=parsed.cascading_steps,
            redaction_count=redaction_count,
            analysis_ms=analysis_ms,
            ecosystem=parsed.ecosystem,
        )

    if pr_number:
        try:
            if placeholder_comment_id:
                await gh.update_pr_comment(
                    installation_id, owner, repo_name, placeholder_comment_id, comment_body
                )
            else:
                placeholder_comment_id = await gh.post_pr_comment(
                    installation_id, owner, repo_name, pr_number, comment_body
                )
        except gh.GitHubAPIError as exc:
            logger.error("Failed to post/update PR comment", error=str(exc))

    # ── Step 11: Persist FailureAnalysis record ────────────────────────────────
    failure_analysis = FailureAnalysis(
        run_id=workflow_run.id,
        error_category=category,
        failed_step=parsed.root_step.name if parsed.root_step else None,
        cascading_steps=json.dumps(parsed.cascading_steps),
        root_cause=root_cause,
        fix_suggestion=fix,
        confidence=confidence,
        source=analysis_source,
        rule_id=rule_id,
        redaction_count=redaction_count,
    )
    db.add(failure_analysis)

    # ── Step 12: Mark WorkflowRun as completed ─────────────────────────────────
    workflow_run.status = "completed"
    workflow_run.analyzed_at = datetime.now(timezone.utc)
    workflow_run.analysis_ms = analysis_ms
    workflow_run.comment_posted = pr_number is not None

    logger.info(
        "Analysis pipeline complete",
        run_id=str(workflow_run.id),
        source=analysis_source,
        analysis_ms=analysis_ms,
        comment_posted=workflow_run.comment_posted,
    )


async def _mark_run_failed(
    db: AsyncSession, github_run_id: int, reason: str
) -> None:
    result = await db.execute(
        select(WorkflowRun).where(WorkflowRun.github_run_id == github_run_id)
    )
    run = result.scalar_one_or_none()
    if run:
        run.status = "failed"
        run.error_detail = reason
        await db.commit()


# ── Webhook entry point ───────────────────────────────────────────────────────

@router.post("/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
):
    settings = get_settings()
    raw_body = await request.body()

    if not verify_webhook_signature(
        raw_body, x_hub_signature_256 or "", settings.github_webhook_secret
    ):
        logger.warning(
            "Webhook signature verification failed",
            delivery_id=x_github_delivery,
            event=x_github_event,
        )
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info(
        "Webhook received",
        event=x_github_event,
        delivery_id=x_github_delivery,
        action=payload.get("action"),
    )

    match x_github_event:
        case "workflow_run":
            action = payload.get("action")
            conclusion = payload.get("workflow_run", {}).get("conclusion")

            if action == "completed" and conclusion == "failure":
                installation_id = payload.get("installation", {}).get("id")
                if not installation_id:
                    logger.error("workflow_run payload missing installation.id")
                else:
                    background_tasks.add_task(
                        _run_analysis_pipeline,
                        payload=payload,
                        installation_id=installation_id,
                    )
                    logger.info(
                        "Workflow run queued for analysis",
                        run_id=payload.get("workflow_run", {}).get("id"),
                        repo=payload.get("repository", {}).get("full_name"),
                    )
            else:
                logger.debug(
                    "Skipping workflow_run",
                    action=action,
                    conclusion=conclusion,
                )

        case "installation":
            action = payload.get("action")
            inst = payload.get("installation", {})
            logger.info(
                "Installation event",
                action=action,
                installation_id=inst.get("id"),
                account=inst.get("account", {}).get("login"),
            )

        case "installation_repositories":
            logger.info(
                "Repositories changed",
                added=len(payload.get("repositories_added", [])),
                removed=len(payload.get("repositories_removed", [])),
            )

        case "ping":
            logger.info("GitHub ping — webhook configured correctly")

        case _:
            logger.debug("Unhandled event", event=x_github_event)

    return {"status": "accepted", "delivery_id": x_github_delivery}


import json  # noqa: E402 — used in pipeline, placed here to avoid circular at module top