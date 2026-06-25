import hashlib
import hmac
import json
import time

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from config import get_settings
from db import get_db
from logger import logger
from models.database import WorkflowRun, Repository

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def verify_webhook_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    """
    Verify GitHub's HMAC-SHA256 webhook signature.
    Uses compare_digest to prevent timing attacks.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


async def _process_workflow_run(
    payload: dict,
    installation_id: int,
    db: AsyncSession,
) -> None:
    """
    Background task: full analysis pipeline for a failed workflow run.
    Runs after the webhook endpoint has already returned 200 to GitHub.
    """
    run_data = payload.get("workflow_run", {})
    github_run_id = run_data.get("id")
    repo_data = payload.get("repository", {})

    with logger.contextualize(
        github_run_id=github_run_id,
        installation_id=installation_id,
        repo=repo_data.get("full_name"),
    ):
        try:
            # ── Idempotency check ──────────────────────────────────────────────
            existing = await db.execute(
                select(WorkflowRun).where(
                    WorkflowRun.github_run_id == github_run_id
                )
            )
            if existing.scalar_one_or_none():
                logger.info("Skipping — run already processed (idempotency guard)")
                return

            logger.info(
                "Starting workflow run analysis",
                workflow=run_data.get("name"),
                conclusion=run_data.get("conclusion"),
                pr_count=len(run_data.get("pull_requests", [])),
            )

            # ── Placeholder: full pipeline goes here in Step 3 ────────────────
            # For now: log the payload summary so we can verify webhooks work
            logger.info(
                "Webhook payload received — pipeline not yet wired",
                head_sha=run_data.get("head_sha"),
                pr_number=(
                    run_data["pull_requests"][0]["number"]
                    if run_data.get("pull_requests")
                    else None
                ),
                logs_url=run_data.get("logs_url"),
            )

        except Exception as exc:
            logger.error(
                "Unhandled error in background analysis",
                error=str(exc),
                exc_info=True,
            )


@router.post("/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    start_time = time.monotonic()

    # ── 1. Read raw body (must be before any parsing) ─────────────────────────
    raw_body = await request.body()

    # ── 2. Verify signature — reject before doing anything else ───────────────
    if not verify_webhook_signature(
        raw_body, x_hub_signature_256 or "", settings.github_webhook_secret
    ):
        logger.warning(
            "Webhook signature verification failed",
            delivery_id=x_github_delivery,
            event=x_github_event,
            sig_present=bool(x_hub_signature_256),
        )
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    # ── 3. Parse payload ───────────────────────────────────────────────────────
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.error("Webhook payload is not valid JSON", delivery_id=x_github_delivery)
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # ── 4. Log every event for debugging — remove in production ───────────────
    logger.info(
        "Webhook received",
        event=x_github_event,
        delivery_id=x_github_delivery,
        action=payload.get("action"),
        elapsed_ms=round((time.monotonic() - start_time) * 1000),
    )

    # ── 5. Route by event type ─────────────────────────────────────────────────
    match x_github_event:
        case "workflow_run":
            await _handle_workflow_run(payload, background_tasks, db)

        case "installation":
            await _handle_installation(payload, db)

        case "installation_repositories":
            await _handle_installation_repositories(payload, db)

        case "check_run":
            # Reserved for future use
            pass

        case "ping":
            logger.info("GitHub ping received — webhook configured correctly")

        case _:
            logger.debug("Unhandled event type", event=x_github_event)

    # ── 6. Always return 200 immediately — GitHub retries on non-200 ──────────
    return {"status": "accepted", "delivery_id": x_github_delivery}


async def _handle_workflow_run(
    payload: dict,
    background_tasks: BackgroundTasks,
    db: AsyncSession,
) -> None:
    action = payload.get("action")
    run = payload.get("workflow_run", {})
    conclusion = run.get("conclusion")

    # Only process completed failures — ignore queued/in_progress/success
    if action != "completed" or conclusion != "failure":
        logger.debug(
            "Skipping workflow_run — not a completed failure",
            action=action,
            conclusion=conclusion,
        )
        return

    installation_id = payload.get("installation", {}).get("id")
    if not installation_id:
        logger.error("workflow_run payload missing installation.id")
        return

    # Kick off background analysis — returns immediately so we can 200 GitHub
    background_tasks.add_task(
        _process_workflow_run,
        payload=payload,
        installation_id=installation_id,
        db=db,
    )

    logger.info(
        "Workflow run failure queued for analysis",
        run_id=run.get("id"),
        workflow=run.get("name"),
        repo=payload.get("repository", {}).get("full_name"),
    )


async def _handle_installation(payload: dict, db: AsyncSession) -> None:
    action = payload.get("action")
    installation = payload.get("installation", {})
    installation_id = installation.get("id")
    account = installation.get("account", {})

    logger.info(
        "Installation event",
        action=action,
        installation_id=installation_id,
        account=account.get("login"),
    )

    # Full user creation + installation record goes in Step 5 (OAuth + dashboard)
    # For now: log so we can see installs happening
    if action == "deleted":
        logger.warning(
            "App uninstalled",
            installation_id=installation_id,
            account=account.get("login"),
        )
    elif action in ("suspend", "unsuspend"):
        logger.info(
            "Installation suspension change",
            action=action,
            installation_id=installation_id,
        )


async def _handle_installation_repositories(
    payload: dict, db: AsyncSession
) -> None:
    action = payload.get("action")
    installation_id = payload.get("installation", {}).get("id")
    added = payload.get("repositories_added", [])
    removed = payload.get("repositories_removed", [])

    logger.info(
        "Installation repositories changed",
        action=action,
        installation_id=installation_id,
        added=[r.get("full_name") for r in added],
        removed=[r.get("full_name") for r in removed],
    )