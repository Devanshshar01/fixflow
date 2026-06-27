"""
Webhook router — the core of FixFlow.

Flow per failed workflow_run:
1.  Verify HMAC-SHA256 signature
2.  Return 200 immediately
3.  Background task:
    a.  Idempotency check
    b.  Upsert repository record
    c.  Create WorkflowRun (status=analyzing)
    d.  Post placeholder PR comment
    e.  Download + parse logs
    f.  Redact secrets
    g.  Rule engine → increment hit count on match
    h.  AI fallback if no rule match
    i.  Format and post/update PR comment
    j.  Persist FailureAnalysis
    k.  Mark WorkflowRun status=completed
"""
from sqlalchemy import select

from db import AsyncSessionLocal
from models.database import User, Installation, Repository
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


# ── Core analysis pipeline ─────────────────────────────────────────────────────

async def _run_analysis_pipeline(
    payload: dict,
    installation_id: int,
) -> None:
    """
    Full analysis pipeline. Opens its own DB session because BackgroundTasks
    run after the request/response cycle is closed.
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
                    "Analysis pipeline failed — unhandled exception",
                    error=str(exc),
                    exc_info=True,
                )
                async with AsyncSessionLocal() as err_db:
                    await _mark_run_failed(err_db, github_run_id, str(exc))
                    await err_db.commit()


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

    # ── 1. Idempotency ─────────────────────────────────────────────────────────
    existing = await db.execute(
        select(WorkflowRun).where(WorkflowRun.github_run_id == github_run_id)
    )
    if existing.scalar_one_or_none():
        logger.info("Skipping — already processed (idempotency guard)")
        return

    # ── 2. Resolve installation ────────────────────────────────────────────────
    inst_result = await db.execute(
        select(Installation).where(Installation.installation_id == installation_id)
    )
    installation_obj = inst_result.scalar_one_or_none()

    # ── 3. Upsert repository ───────────────────────────────────────────────────
    repo_result = await db.execute(
        select(Repository).where(Repository.github_repo_id == repo_data.get("id"))
    )
    repo_obj = repo_result.scalar_one_or_none()

    if not repo_obj:
        repo_obj = Repository(
            installation_id=installation_obj.id if installation_obj else None,
            github_repo_id=repo_data["id"],
            full_name=repo_data["full_name"],
            default_branch=repo_data.get("default_branch", "main"),
        )
        db.add(repo_obj)
        await db.flush()
        logger.info("New repository registered", repo=repo_data["full_name"])

    # ── 4. Create WorkflowRun ──────────────────────────────────────────────────
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
        "WorkflowRun record created",
        run_id=str(workflow_run.id),
        pr_number=pr_number,
        workflow=run_data.get("name"),
    )

    # ── 5. Placeholder PR comment ──────────────────────────────────────────────
    placeholder_comment_id: int | None = None
    if pr_number:
        existing_cid = await gh.find_existing_fixflow_comment(
            installation_id, owner, repo_name, pr_number
        )
        if existing_cid:
            placeholder_comment_id = existing_cid
            logger.info("Reusing existing FixFlow comment", comment_id=existing_cid)
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

    # ── 6. Download logs ───────────────────────────────────────────────────────
    try:
        zip_bytes = await gh.download_logs_zip(
            installation_id, owner, repo_name, github_run_id
        )
    except gh.LogsNotFoundError:
        logger.warning("Logs not found — may have expired (>90 days)")
        workflow_run.status = "failed"
        workflow_run.error_detail = "logs_expired"
        return
    except gh.GitHubAPIError as exc:
        logger.error("Log download failed", error=str(exc))
        workflow_run.status = "failed"
        workflow_run.error_detail = f"log_download_failed: {exc}"
        return

    # ── 7. Parse logs ──────────────────────────────────────────────────────────
    parsed = parse_log_zip(zip_bytes)

    # ── 8. Redact ──────────────────────────────────────────────────────────────
    redaction_result = redact(parsed.snippet)
    clean_snippet = redaction_result.text
    redaction_count = redaction_result.count

    logger.info(
        "Redaction complete",
        count=redaction_count,
        categories=redaction_result.categories,
    )

    # ── 9. Rule engine ─────────────────────────────────────────────────────────
    rule_result = rule_match(clean_snippet, parsed.ecosystem)

    analysis_source = "unknown"
    root_cause = "Unable to determine root cause"
    fix = "Review the log snippet above for more details"
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

        # Increment hit count — non-blocking
        await increment_hit_count(rule_id, db)

        logger.info(
            "Rule engine resolved failure",
            rule_id=rule_id,
            category=category,
        )

    else:
        # ── 10. AI fallback ────────────────────────────────────────────────────
        logger.info("No rule match — falling back to AI analyzer")
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

    # ── 11. Build comment ──────────────────────────────────────────────────────
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

    # ── 12. Post/update PR comment ─────────────────────────────────────────────
    if pr_number:
        try:
            if placeholder_comment_id:
                await gh.update_pr_comment(
                    installation_id, owner, repo_name,
                    placeholder_comment_id, comment_body,
                )
            else:
                placeholder_comment_id = await gh.post_pr_comment(
                    installation_id, owner, repo_name, pr_number, comment_body,
                )
        except gh.GitHubAPIError as exc:
            logger.error("Failed to post/update PR comment", error=str(exc))

    # ── 13. Persist FailureAnalysis ────────────────────────────────────────────
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

    # ── 14. Mark WorkflowRun completed ────────────────────────────────────────
    workflow_run.status = "completed"
    workflow_run.analyzed_at = datetime.now(timezone.utc)
    workflow_run.analysis_ms = analysis_ms
    workflow_run.comment_posted = pr_number is not None

    logger.info(
        "Pipeline complete",
        run_id=str(workflow_run.id),
        source=analysis_source,
        category=category,
        analysis_ms=analysis_ms,
        comment_posted=workflow_run.comment_posted,
        redactions=redaction_count,
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
        run.error_detail = reason[:500]


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

            if action != "created":
                return

            installation_data = payload["installation"]
            account = payload["installation"]["account"]

            async with AsyncSessionLocal() as db:

                result = await db.execute(
                    select(User).where(User.github_id == account["id"])
                )

                user = result.scalar_one_or_none()

                if not user:
                    user = User(
                        github_id=account["id"],
                        login=account["login"],
                        name=account.get("name"),
                        avatar_url=account.get("avatar_url"),
                    )

                    db.add(user)
                    await db.flush()

                result = await db.execute(
                    select(Installation).where(
                        Installation.installation_id == installation_data["id"]
                    )
                )

                existing_installation = result.scalar_one_or_none()

                if not existing_installation:
                    db.add(
                        Installation(
                            installation_id=installation_data["id"],
                            user_id=user.id,
                            account_login=account["login"],
                            account_type=account["type"],
                        )
                    )

                await db.commit()

            logger.info(
                "Installation saved",
                installation_id=installation_data["id"],
                account=account["login"],
            )

        case "installation_repositories":
            installation_data = payload["installation"]

            logger.info(
                "installation_repositories received",
                installation_id=installation_data["id"],
                repos_added=len(payload.get("repositories_added", [])),
            )

            async with AsyncSessionLocal() as db:

                result = await db.execute(
                    select(Installation).where(
                        Installation.installation_id == installation_data["id"]
                    )
                )

                installation = result.scalar_one_or_none()

                if not installation:
                    logger.error(
                        "Installation not found",
                        installation_id=installation_data["id"],
                    )
                    return

                logger.info(
                    "Installation found",
                    installation_id=installation.installation_id,
                    db_id=str(installation.id),
                )

                for repo in payload.get("repositories_added", []):

                    logger.info(
                        "Processing repository",
                        repo_name=repo["full_name"],
                        repo_id=repo["id"],
                    )

                    existing = await db.execute(
                        select(Repository).where(
                            Repository.github_repo_id == repo["id"]
                        )
                    )

                    if existing.scalar_one_or_none():
                        logger.info(
                            "Repository already exists",
                            repo_id=repo["id"],
                        )
                        continue

                    db.add(
                        Repository(
                            installation_id=installation.id,
                            github_repo_id=repo["id"],
                            full_name=repo["full_name"],
                            default_branch=repo.get(
                                "default_branch",
                                "main",
                            ),
                            is_active=True,
                        )
                    )

                    logger.info(
                        "Repository queued for insert",
                        repo_name=repo["full_name"],
                    )

                await db.commit()

            logger.info(
                "Repositories saved",
                count=len(payload.get("repositories_added", [])),
            )

        case "ping":
            logger.info("GitHub ping — webhook configured correctly")

        case _:
            logger.debug("Unhandled event", event=x_github_event)

    return {"status": "accepted", "delivery_id": x_github_delivery}
