"""
Rule engine — deterministic pattern matching against known CI failure signatures.

Loads patterns from packages/rules/patterns.json.
Returns a match instantly with no AI call required.
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
    confidence: int = 100   # Rule matches are deterministic


_patterns_cache: list[dict] | None = None


def _load_patterns() -> list[dict]:
    global _patterns_cache
    if _patterns_cache is not None:
        return _patterns_cache

    # Walk up from services/ to find packages/rules/patterns.json
    base = Path(__file__).resolve()
    for _ in range(6):
        candidate = base / "packages" / "rules" / "patterns.json"
        if candidate.exists():
            break
        base = base.parent
    else:
        logger.warning("patterns.json not found — rule engine will return no matches")
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

    Patterns are tried in order — put more specific patterns before generic ones
    in patterns.json.
    """
    patterns = _load_patterns()

    for rule in patterns:
        # Skip inactive rules
        if not rule.get("is_active", True):
            continue

        # Optional ecosystem filter — if rule specifies one, only match that ecosystem
        rule_category = rule.get("category", "")
        if rule_category not in ("", "unknown", ecosystem) and ecosystem != "unknown":
            # Still try if there's a clear ecosystem mismatch only when rule is strict
            if rule.get("strict_ecosystem", False):
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

            # Interpolate capture groups into root_cause and fix templates
            root_cause = _interpolate(rule.get("root_cause", ""), match_obj)
            fix = _interpolate(rule.get("fix", ""), match_obj)

            return RuleMatch(
                rule_id=rule.get("id", "unknown"),
                category=rule_category,
                severity=rule.get("severity", "medium"),
                root_cause=root_cause,
                fix=fix,
                fix_url=rule.get("fix_url"),
                prevention=rule.get("prevention"),
            )

    return None


def _interpolate(template: str, match_obj: re.Match) -> str:
    """
    Replace {match[0]}, {match[1]} etc. in templates with regex capture groups.
    """
    result = template
    for i, group in enumerate(match_obj.groups()):
        if group is not None:
            result = result.replace(f"{{match[{i + 1}]}}", group)
    return result


def reload_patterns() -> int:
    """Force-reload patterns from disk. Returns new count."""
    global _patterns_cache
    _patterns_cache = None
    patterns = _load_patterns()
    return len(patterns)