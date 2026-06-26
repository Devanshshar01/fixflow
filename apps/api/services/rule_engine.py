"""
Rule engine — deterministic pattern matching against known CI failure signatures.

Loads patterns from packages/rules/patterns.json at startup and caches them.
On a match, increments hit_count in the error_patterns table if a DB session
is provided. Falls back gracefully if the DB write fails — never blocks analysis.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from logger import logger


@dataclass
class RuleMatch:
    rule_id: str
    category: str
    severity: str
    root_cause: str
    fix: str
    fix_url: str | None
    prevention: str | None
    confidence: int = 100


_patterns_cache: list[dict] | None = None


def _load_patterns() -> list[dict]:
    global _patterns_cache
    if _patterns_cache is not None:
        return _patterns_cache

    base = Path(__file__).resolve()
    for _ in range(8):
        candidate = base / "packages" / "rules" / "patterns.json"
        if candidate.exists():
            break
        base = base.parent
    else:
        logger.warning("patterns.json not found — rule engine disabled")
        _patterns_cache = []
        return _patterns_cache

    with candidate.open() as f:
        _patterns_cache = json.load(f)

    logger.info("Rule engine loaded", pattern_count=len(_patterns_cache))
    return _patterns_cache


def match(log_snippet: str, ecosystem: str = "unknown") -> RuleMatch | None:
    """
    Scan the log snippet against all active patterns.
    Returns the first match, or None if no rule applies.
    """
    patterns = _load_patterns()

    for rule in patterns:
        if not rule.get("is_active", True):
            continue

        rule_category = rule.get("category", "")
        if rule.get("strict_ecosystem", False):
            if rule_category not in ("", "unknown", ecosystem) and ecosystem != "unknown":
                continue

        try:
            compiled = re.compile(rule["pattern"], re.IGNORECASE | re.MULTILINE)
        except re.error as exc:
            logger.warning(
                "Invalid regex in patterns.json — skipping",
                rule_id=rule.get("id"),
                error=str(exc),
            )
            continue

        match_obj = compiled.search(log_snippet)
        if match_obj:
            logger.info(
                "Rule engine matched",
                rule_id=rule.get("id"),
                category=rule_category,
                severity=rule.get("severity"),
            )
            return RuleMatch(
                rule_id=rule.get("id", "unknown"),
                category=rule_category,
                severity=rule.get("severity", "medium"),
                root_cause=_interpolate(rule.get("root_cause", ""), match_obj),
                fix=_interpolate(rule.get("fix", ""), match_obj),
                fix_url=rule.get("fix_url"),
                prevention=rule.get("prevention"),
            )

    return None


async def increment_hit_count(rule_id: str, db) -> None:
    """
    Increment hit_count for a matched rule in the DB.
    Creates the record if it doesn't exist yet (first time this rule fires).
    Never raises — a failed DB write must not block the analysis pipeline.
    """
    from sqlalchemy import select, func
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from models.database import ErrorPattern
    import uuid
    from datetime import datetime, timezone

    try:
        # Try to find existing record by pattern_id
        result = await db.execute(
            select(ErrorPattern).where(ErrorPattern.pattern_id == rule_id)
        )
        pattern_row = result.scalar_one_or_none()

        if pattern_row:
            pattern_row.hit_count += 1
            pattern_row.updated_at = datetime.now(timezone.utc)
        else:
            # First time this rule has fired — seed from patterns.json
            patterns = _load_patterns()
            rule_data = next((p for p in patterns if p.get("id") == rule_id), None)

            if rule_data:
                new_pattern = ErrorPattern(
                    pattern_id=rule_id,
                    pattern=rule_data.get("pattern", ""),
                    category=rule_data.get("category", "unknown"),
                    severity=rule_data.get("severity", "medium"),
                    root_cause=rule_data.get("root_cause", ""),
                    fix=rule_data.get("fix", ""),
                    fix_url=rule_data.get("fix_url"),
                    hit_count=1,
                    is_active=True,
                )
                db.add(new_pattern)

        logger.debug("Rule hit count incremented", rule_id=rule_id)

    except Exception as exc:
        # Non-fatal — log and continue
        logger.warning(
            "Failed to increment rule hit count — non-fatal",
            rule_id=rule_id,
            error=str(exc),
        )


def _interpolate(template: str, match_obj: re.Match) -> str:
    result = template
    for i, group in enumerate(match_obj.groups()):
        if group is not None:
            result = result.replace(f"{{match[{i + 1}]}}", group)
    return result


def reload_patterns() -> int:
    global _patterns_cache
    _patterns_cache = None
    patterns = _load_patterns()
    return len(patterns)