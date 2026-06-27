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
from typing import Any

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


def _preview(value: Any) -> str:
    return repr(value)[:500]


def _log_before_get(context: str, value: Any) -> None:
    logger.info(
        "Before .get()",
        context=context,
        object_type=type(value).__name__,
        object_preview=_preview(value),
    )


def _safe_get(value: Any, key: str, default: Any = None, *, context: str) -> Any:
    _log_before_get(context, value)

    if not isinstance(value, dict):
        logger.warning(
            "Expected dict before .get(); using default",
            context=context,
            key=key,
            object_type=type(value).__name__,
            object_preview=_preview(value),
        )
        return default

    return value.get(key, default)


def _normalize_patterns(raw_patterns: Any) -> list[dict]:
    logger.info(
        "Rule patterns loaded from JSON",
        object_type=type(raw_patterns).__name__,
        object_preview=_preview(raw_patterns),
    )

    normalized: list[dict] = []

    def add_patterns(value: Any, path: str) -> None:
        if isinstance(value, dict):
            normalized.append(value)
            return

        if isinstance(value, list):
            logger.warning(
                "Rule pattern list encountered; flattening",
                path=path,
                object_type=type(value).__name__,
                object_preview=_preview(value),
            )
            for index, item in enumerate(value):
                add_patterns(item, f"{path}[{index}]")
            return

        logger.warning(
            "Ignoring invalid rule pattern entry",
            path=path,
            object_type=type(value).__name__,
            object_preview=_preview(value),
        )

    add_patterns(raw_patterns, "patterns")
    return normalized


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
        _patterns_cache = _normalize_patterns(json.load(f))

    logger.info("Rule engine loaded", pattern_count=len(_patterns_cache))
    return _patterns_cache


def match(log_snippet: str, ecosystem: str = "unknown") -> RuleMatch | None:
    """
    Scan the log snippet against all active patterns.
    Returns the first match, or None if no rule applies.
    """
    patterns = _load_patterns()

    for rule_index, rule in enumerate(patterns):
        if not isinstance(rule, dict):
            logger.warning(
                "Skipping non-dict rule pattern",
                rule_index=rule_index,
                object_type=type(rule).__name__,
                object_preview=_preview(rule),
            )
            continue

        if not _safe_get(rule, "is_active", True, context="rule.is_active"):
            continue

        rule_category = _safe_get(rule, "category", "", context="rule.category")
        if _safe_get(rule, "strict_ecosystem", False, context="rule.strict_ecosystem"):
            if rule_category not in ("", "unknown", ecosystem) and ecosystem != "unknown":
                continue

        try:
            compiled = re.compile(rule["pattern"], re.IGNORECASE | re.MULTILINE)
        except re.error as exc:
            logger.warning(
                "Invalid regex in patterns.json — skipping",
                rule_id=_safe_get(rule, "id", context="rule.id.invalid_regex"),
                error=str(exc),
            )
            continue

        match_obj = compiled.search(log_snippet)
        if match_obj:
            logger.info(
                "Rule engine matched",
                rule_id=_safe_get(rule, "id", context="rule.id.match_log"),
                category=rule_category,
                severity=_safe_get(rule, "severity", context="rule.severity.match_log"),
            )
            return RuleMatch(
                rule_id=_safe_get(rule, "id", "unknown", context="rule.id.result"),
                category=rule_category,
                severity=_safe_get(
                    rule, "severity", "medium", context="rule.severity.result"
                ),
                root_cause=_interpolate(
                    _safe_get(rule, "root_cause", "", context="rule.root_cause"),
                    match_obj,
                ),
                fix=_interpolate(
                    _safe_get(rule, "fix", "", context="rule.fix"),
                    match_obj,
                ),
                fix_url=_safe_get(rule, "fix_url", context="rule.fix_url"),
                prevention=_safe_get(rule, "prevention", context="rule.prevention"),
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
            rule_data = next(
                (
                    p
                    for p in patterns
                    if _safe_get(p, "id", context="rule_data.id.lookup") == rule_id
                ),
                None,
            )

            if rule_data:
                new_pattern = ErrorPattern(
                    pattern_id=rule_id,
                    pattern=_safe_get(
                        rule_data, "pattern", "", context="rule_data.pattern"
                    ),
                    category=_safe_get(
                        rule_data, "category", "unknown", context="rule_data.category"
                    ),
                    severity=_safe_get(
                        rule_data, "severity", "medium", context="rule_data.severity"
                    ),
                    root_cause=_safe_get(
                        rule_data, "root_cause", "", context="rule_data.root_cause"
                    ),
                    fix=_safe_get(rule_data, "fix", "", context="rule_data.fix"),
                    fix_url=_safe_get(
                        rule_data, "fix_url", context="rule_data.fix_url"
                    ),
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
