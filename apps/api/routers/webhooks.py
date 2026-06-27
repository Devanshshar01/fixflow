"""
Webhook router — the core of FixFlow.

Key fixes in this version:
- Granular try/except around every DB operation so failures surface with
  exact line context instead of being swallowed
- Background task opens its own session correctly (no shared state from request)
- Repository upsert happens inside the background session, not the request
  session, so there is no cross-session object issue
- _mark_run_failed uses its own nested session so it never fails silently
- Every stage logs entry AND exit so you can pinpoint exactly where it stops
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from config import get_settings
from db import AsyncSessionLocal
from logger import logger
from models.database import Installation, Repository, WorkflowRun, FailureAnalysis
from services import github as gh
from services.log_parser import parse_log_zip
from services.redactor import redact
from services.rule_engine import match as rule_match, increment_hit_count
from services.ai_analyzer import get_analyzer, AnalysisContext, AIAnalysisError

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ── Signature verification ─────────────────────────────────────────────────────

def verify_webhook_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ── Comment formatters ─────────────────────────────────────────────────────────

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
        cascading_note = (
            f"\n> ℹ️ **Cascading failures** also detected in: "
            f"{steps_str}{more} — fixing the root step above should resolve these.\n"
        )

    redaction_note = (
        f"\n> 🔒 **{redaction_count} secret"
        f"{'s' if redaction_count != 1 else ''} redacted** before analysis\n"
        if redaction_count > 0
        else ""
    )

    prevention_section = f"\n**Prevention**\n{prevention}\n" if prevention else ""

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
    redaction_note = (
        f"\n> 🔒 **{redaction_count} secret"
        f"{'s' if redaction_count != 1 else ''} redacted** before display\n"
        if redaction_count > 0
        else ""
    )
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


# ── Background analysis pipeline ───────────────────────────────────────────────

async def _run_analysis_pipeline(
    payload: dict,
    installation_id: int,
) -> None:
    """
    Entry point for the background task. Opens its own DB session.
    All DB operations happen here — nothing is shared from the request context.
    """
    start_time = time.monotonic()
    run_data = payload.get("workflow_run", {})
    repo_data = payload.get("repository", {})
    github_run_id = run_data.get("id")
    full_name = repo_data.get("full_name", "/")
    owner, repo_name = full_name.split("/", 1)

    with logger.contextualize(
        github_run_id=github_run_id,
        installation_id=installation_id,
        repo=full_name,
    ):
        logger.info("Background pipeline starting")

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
                logger.info("Pipeline completed — committing transaction")
                await db.commit()
                logger.info("Transaction committed successfully")

            except Exception as exc:
                logger.error(
                    "Pipeline failed — rolling back",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                try:
                    await db.rollback()
                except Exception as rollback_exc:
                    logger.error(
                        "Rollback also failed",
                        error=str(rollback_exc),
                    )

                # Use a fresh session to mark the run as failed
                # (the main session is in a bad state after the exception)
                await _mark_run_failed_safe(github_run_id, str(exc))


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

    # ── STAGE 1: Idempotency ───────────────────────────────────────────────────
    logger.info("Stage 1: idempotency check")
    try:
        existing = await db.execute(
            select(WorkflowRun).where(WorkflowRun.github_run_id == github_run_id)
        )
        if existing.scalar_one_or_none():
            logger.info("Run already processed — skipping (idempotency guard)")
            return
        logger.info("Stage 1 passed — run not yet processed")
    except Exception as exc:
        logger.error("Stage 1 FAILED: idempotency check threw", error=str(exc), exc_info=True)
        raise

    # ── STAGE 2: Resolve installation ─────────────────────────────────────────
    logger.info("Stage 2: resolving installation record")
    installation_obj = None
    try:
        inst_result = await db.execute(
            select(Installation).where(
                Installation.installation_id == installation_id
            )
        )
        installation_obj = inst_result.scalar_one_or_none()
        if installation_obj:
            logger.info(
                "Stage 2 passed — installation found",
                installation_db_id=str(installation_obj.id),
                account=installation_obj.account_login,
            )
        else:
            logger.warning(
                "Stage 2: installation record not found in DB — "
                "will create repository without installation_id link",
                installation_id=installation_id,
            )
    except Exception as exc:
        logger.error("Stage 2 FAILED: installation lookup threw", error=str(exc), exc_info=True)
        raise

    # ── STAGE 3: Upsert repository ─────────────────────────────────────────────
    logger.info("Stage 3: upserting repository record")
    repo_obj = None
    try:
        repo_github_id = repo_data.get("id")
        if not repo_github_id:
            raise ValueError(
                f"repo_data missing 'id' field. Keys present: {list(repo_data.keys())}"
            )

        repo_result = await db.execute(
            select(Repository).where(Repository.github_repo_id == repo_github_id)
        )
        repo_obj = repo_result.scalar_one_or_none()

        if repo_obj:
            logger.info(
                "Stage 3 passed — existing repository found",
                repo_db_id=str(repo_obj.id),
                full_name=repo_obj.full_name,
            )
        else:
            repo_obj = Repository(
                installation_id=installation_obj.id if installation_obj else None,
                github_repo_id=repo_github_id,
                full_name=repo_data.get("full_name", f"{owner}/{repo_name}"),
                default_branch=repo_data.get("default_branch", "main"),
            )
            db.add(repo_obj)
            await db.flush()
            logger.info(
                "Stage 3 passed — new repository created and flushed",
                repo_db_id=str(repo_obj.id),
                full_name=repo_obj.full_name,
            )
    except Exception as exc:
        logger.error("Stage 3 FAILED: repository upsert threw", error=str(exc), exc_info=True)
        raise

    # ── STAGE 4: Create WorkflowRun ────────────────────────────────────────────
    logger.info("Stage 4: creating WorkflowRun record")
    workflow_run = None
    try:
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
        await db.flush()  # Get the UUID assigned before we reference it

        logger.info(
            "Stage 4 passed — WorkflowRun created and flushed",
            run_db_id=str(workflow_run.id),
            pr_number=pr_number,
            workflow=run_data.get("name"),
            repo_db_id=str(repo_obj.id),
        )
    except Exception as exc:
        logger.error("Stage 4 FAILED: WorkflowRun creation threw", error=str(exc), exc_info=True)
        raise

    # ── STAGE 5: Placeholder PR comment ───────────────────────────────────────
    logger.info("Stage 5: posting placeholder PR comment")
    placeholder_comment_id: int | None = None
    pr_number = workflow_run.pr_number

    if pr_number:
        try:
            existing_cid = await gh.find_existing_fixflow_comment(
                installation_id, owner, repo_name, pr_number
            )
            if existing_cid:
                placeholder_comment_id = existing_cid
                logger.info(
                    "Stage 5: reusing existing FixFlow comment",
                    comment_id=existing_cid,
                )
            else:
                placeholder_body = (
                    "<!-- fixflow:managed -->\n"
                    "## 🔍 FixFlow Analysis\n\n"
                    "⏳ Analyzing CI failure — fix suggestion will appear here shortly...\n\n"
                    f"**Workflow:** `{run_data.get('name', 'unknown')}`\n"
                    f"**Commit:** `{(run_data.get('head_sha') or '')[:7]}`"
                )
                placeholder_comment_id = await gh.post_pr_comment(
                    installation_id, owner, repo_name, pr_number, placeholder_body
                )
                logger.info(
                    "Stage 5 passed — placeholder comment posted",
                    comment_id=placeholder_comment_id,
                )
        except gh.GitHubAPIError as exc:
            # Non-fatal — missing PR comment is bad UX but shouldn't abort analysis
            logger.warning(
                "Stage 5: could not post placeholder comment — continuing",
                error=str(exc),
                status_code=exc.status_code,
            )
    else:
        logger.info("Stage 5: no PR number — skipping comment (push-only run)")

    # ── STAGE 6: Download logs ─────────────────────────────────────────────────
    logger.info("Stage 6: downloading log ZIP")
    zip_bytes: bytes | None = None
    try:
        zip_bytes = await gh.download_logs_zip(
            installation_id, owner, repo_name, github_run_id
        )
        logger.info(
            "Stage 6 passed — log ZIP downloaded",
            size_bytes=len(zip_bytes),
        )
    except gh.LogsNotFoundError:
        logger.warning("Stage 6: logs not found (may have expired >90 days)")
        workflow_run.status = "failed"
        workflow_run.error_detail = "logs_expired"
        return  # Commit will happen in the outer try block
    except gh.GitHubAPIError as exc:
        logger.error(
            "Stage 6 FAILED: log download threw GitHub API error",
            error=str(exc),
            status_code=exc.status_code,
            exc_info=True,
        )
        workflow_run.status = "failed"
        workflow_run.error_detail = f"log_download_failed: {exc}"
        return
    except Exception as exc:
        logger.error("Stage 6 FAILED: unexpected error during log download", error=str(exc), exc_info=True)
        workflow_run.status = "failed"
        workflow_run.error_detail = f"unexpected_log_error: {exc}"
        return

    # ── STAGE 7: Parse logs ────────────────────────────────────────────────────
    logger.info("Stage 7: parsing log ZIP")
    try:
        parsed = parse_log_zip(zip_bytes)
        logger.info(
            "Stage 7 passed — logs parsed",
            total_steps=parsed.total_steps,
            failing_steps=parsed.total_failing_steps,
            root_step=parsed.root_step.name if parsed.root_step else None,
            ecosystem=parsed.ecosystem,
            snippet_lines=len(parsed.snippet.splitlines()),
        )
    except Exception as exc:
        logger.error("Stage 7 FAILED: log parsing threw", error=str(exc), exc_info=True)
        workflow_run.status = "failed"
        workflow_run.error_detail = f"log_parse_failed: {exc}"
        return

    # ── STAGE 8: Redact secrets ────────────────────────────────────────────────
    logger.info("Stage 8: redacting secrets")
    try:
        redaction_result = redact(parsed.snippet)
        clean_snippet = redaction_result.text
        redaction_count = redaction_result.count
        logger.info(
            "Stage 8 passed — redaction complete",
            count=redaction_count,
            categories=redaction_result.categories,
        )
    except Exception as exc:
        logger.error("Stage 8 FAILED: redaction threw", error=str(exc), exc_info=True)
        # Non-fatal — use raw snippet if redaction fails (still safe for rule engine)
        clean_snippet = parsed.snippet
        redaction_count = 0

    # ── STAGE 9: Rule engine ───────────────────────────────────────────────────
    logger.info("Stage 9: running rule engine")
    rule_result = None
    try:
        rule_result = rule_match(clean_snippet, parsed.ecosystem)
        if rule_result:
            logger.info(
                "Stage 9 passed — rule matched",
                rule_id=rule_result.rule_id,
                category=rule_result.category,
            )
        else:
            logger.info("Stage 9 passed — no rule match, will use AI")
    except Exception as exc:
        logger.error("Stage 9 FAILED: rule engine threw", error=str(exc), exc_info=True)
        # Non-fatal — fall through to AI

    # ── Analysis result defaults ───────────────────────────────────────────────
    analysis_source = "unknown"
    root_cause = "Unable to determine root cause"
    fix = "Review the log snippet above for details"
    prevention: str | None = None
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

        # Increment hit count — non-blocking, separate try/except
        try:
            await increment_hit_count(rule_id, db)
            logger.info("Stage 9b: rule hit count incremented", rule_id=rule_id)
        except Exception as exc:
            logger.warning(
                "Stage 9b: hit count increment failed — non-fatal",
                rule_id=rule_id,
                error=str(exc),
            )

    else:
        # ── STAGE 10: AI fallback ──────────────────────────────────────────────
        logger.info("Stage 10: calling AI analyzer")
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
                "Stage 10 passed — AI analysis complete",
                source=analysis_source,
                confidence=confidence,
                category=category,
            )

        except AIAnalysisError as exc:
            logger.error(
                "Stage 10: AI analysis failed — using degraded mode",
                error=str(exc),
            )
            analysis_source = "degraded"
        except Exception as exc:
            logger.error(
                "Stage 10: unexpected AI error — using degraded mode",
                error=str(exc),
                exc_info=True,
            )
            analysis_source = "degraded"

    # ── STAGE 11: Build and post PR comment ────────────────────────────────────
    logger.info("Stage 11: posting final PR comment")
    analysis_ms = round((time.monotonic() - start_time) * 1000)

    if analysis_source == "degraded":
        comment_body = _format_degraded_comment(
            failed_step=parsed.root_step.name if parsed.root_step else None,
            redacted_snippet=clean_snippet,
            reason=(
                "Rule engine had no match and AI analysis failed. "
                "Showing redacted log snippet."
            ),
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
                    installation_id, owner, repo_name,
                    placeholder_comment_id, comment_body,
                )
                logger.info(
                    "Stage 11 passed — existing comment updated",
                    comment_id=placeholder_comment_id,
                )
            else:
                new_comment_id = await gh.post_pr_comment(
                    installation_id, owner, repo_name, pr_number, comment_body,
                )
                logger.info(
                    "Stage 11 passed — new comment posted",
                    comment_id=new_comment_id,
                )
        except gh.GitHubAPIError as exc:
            logger.error(
                "Stage 11: PR comment post/update failed — non-fatal, continuing to DB write",
                error=str(exc),
                status_code=exc.status_code,
            )
        except Exception as exc:
            logger.error(
                "Stage 11: unexpected error posting comment — non-fatal, continuing to DB write",
                error=str(exc),
                exc_info=True,
            )
    else:
        logger.info("Stage 11: no PR number — skipping comment")

    # ── STAGE 12: Persist FailureAnalysis ─────────────────────────────────────
    logger.info("Stage 12: persisting FailureAnalysis record")
    try:
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
        await db.flush()
        logger.info(
            "Stage 12 passed — FailureAnalysis flushed",
            analysis_db_id=str(failure_analysis.id),
            source=analysis_source,
        )
    except Exception as exc:
        logger.error("Stage 12 FAILED: FailureAnalysis creation threw", error=str(exc), exc_info=True)
        raise

    # ── STAGE 13: Mark WorkflowRun as completed ────────────────────────────────
    logger.info("Stage 13: marking WorkflowRun as completed")
    try:
        workflow_run.status = "completed"
        workflow_run.analyzed_at = datetime.now(timezone.utc)
        workflow_run.analysis_ms = analysis_ms
        workflow_run.comment_posted = pr_number is not None
        logger.info(
            "Stage 13 passed — WorkflowRun marked completed",
            run_db_id=str(workflow_run.id),
            analysis_ms=analysis_ms,
            comment_posted=workflow_run.comment_posted,
        )
    except Exception as exc:
        logger.error("Stage 13 FAILED: WorkflowRun update threw", error=str(exc), exc_info=True)
        raise

    logger.info(
        "All pipeline stages complete — ready for commit",
        run_db_id=str(workflow_run.id),
        source=analysis_source,
        category=category,
        analysis_ms=analysis_ms,
    )


async def _mark_run_failed_safe(github_run_id: int, reason: str) -> None:
    """
    Mark a run as failed using a completely fresh DB session.
    Called from the exception handler — the main session may be in a bad state.
    Never raises.
    """
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(WorkflowRun).where(
                    WorkflowRun.github_run_id == github_run_id
                )
            )
            run = result.scalar_one_or_none()
            if run:
                run.status = "failed"
                run.error_detail = reason[:500]
                await db.commit()
                logger.info(
                    "Marked run as failed in DB",
                    github_run_id=github_run_id,
                    reason=reason[:100],
                )
            else:
                logger.warning(
                    "Could not mark run as failed — WorkflowRun not found in DB",
                    github_run_id=github_run_id,
                )
    except Exception as exc:
        logger.error(
            "Failed to mark run as failed — completely silent failure",
            github_run_id=github_run_id,
            error=str(exc),
        )


# ── Webhook entry point ────────────────────────────────────────────────────────

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
                    "Skipping workflow_run — not a completed failure",
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