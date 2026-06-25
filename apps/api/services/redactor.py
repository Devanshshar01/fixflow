"""
PII and secrets redactor.

Runs on every log snippet BEFORE:
- Storing in the database
- Sending to any external AI API
- Posting in PR comments

Also surfaces the redaction count in PR comments as a trust signal.
"""

import re
from dataclasses import dataclass

from logger import logger


@dataclass
class RedactionResult:
    text: str
    count: int
    categories: list[str]   # Which types were found, for logging


# ── Patterns ordered from most specific to least specific ─────────────────────
# Each tuple: (compiled_regex, replacement_label, category_name)

_REDACTION_RULES: list[tuple[re.Pattern, str, str]] = [
    # GitHub tokens
    (re.compile(r"ghp_[a-zA-Z0-9]{36}", re.I), "[GITHUB_PAT]", "github_token"),
    (re.compile(r"gho_[a-zA-Z0-9]{36}", re.I), "[GITHUB_OAUTH_TOKEN]", "github_token"),
    (re.compile(r"ghs_[a-zA-Z0-9]{36}", re.I), "[GITHUB_SERVER_TOKEN]", "github_token"),
    (re.compile(r"ghu_[a-zA-Z0-9]{36}", re.I), "[GITHUB_USER_TOKEN]", "github_token"),
    (re.compile(r"ghr_[a-zA-Z0-9]{36}", re.I), "[GITHUB_REFRESH_TOKEN]", "github_token"),

    # AWS
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[AWS_ACCESS_KEY_ID]", "aws_key"),
    (re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"), "[AWS_SECRET_KEY]", "aws_secret"),

    # Generic API keys (sk-, pk-, rk-)
    (re.compile(r"sk-[a-zA-Z0-9]{20,}", re.I), "[API_SECRET_KEY]", "api_key"),
    (re.compile(r"pk-[a-zA-Z0-9]{20,}", re.I), "[API_PUBLIC_KEY]", "api_key"),

    # Database URLs
    (re.compile(r"postgres(?:ql)?://[^\s\"']+", re.I), "[DATABASE_URL]", "database_url"),
    (re.compile(r"mysql://[^\s\"']+", re.I), "[DATABASE_URL]", "database_url"),
    (re.compile(r"mongodb(?:\+srv)?://[^\s\"']+", re.I), "[DATABASE_URL]", "database_url"),
    (re.compile(r"redis://[^\s\"']+", re.I), "[REDIS_URL]", "database_url"),

    # Bearer / Authorization headers
    (re.compile(r"Bearer\s+[a-zA-Z0-9\-._~+/]+=*", re.I), "Bearer [BEARER_TOKEN]", "bearer_token"),
    (re.compile(r"Authorization:\s*[^\s\n]+", re.I), "Authorization: [REDACTED]", "auth_header"),

    # Private keys (PEM blocks)
    (re.compile(
        r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA |EC )?PRIVATE KEY-----",
        re.MULTILINE,
    ), "[PRIVATE_KEY_BLOCK]", "private_key"),

    # JWT tokens (3 base64 segments separated by dots)
    (re.compile(
        r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"
    ), "[JWT_TOKEN]", "jwt"),

    # Emails
    (re.compile(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
    ), "[EMAIL]", "email"),

    # Generic secrets in key=value format
    (re.compile(
        r"(?:password|passwd|secret|token|api_key|apikey|access_key)\s*[=:]\s*[^\s\n\"']{8,}",
        re.I,
    ), r"[REDACTED_CREDENTIAL]", "generic_secret"),
]


def redact(text: str) -> RedactionResult:
    """
    Apply all redaction rules to the input text.
    Returns the cleaned text, count of replacements made, and categories found.
    """
    result = text
    total_count = 0
    found_categories: set[str] = set()

    for pattern, replacement, category in _REDACTION_RULES:
        new_result, n = pattern.subn(replacement, result)
        if n > 0:
            total_count += n
            found_categories.add(category)
            result = new_result

    if total_count > 0:
        logger.info(
            "Secrets redacted from log",
            count=total_count,
            categories=list(found_categories),
        )

    return RedactionResult(
        text=result,
        count=total_count,
        categories=sorted(found_categories),
    )


def redact_for_display(text: str) -> RedactionResult:
    """
    Alias for redact() — use this to make call sites self-documenting
    about intent (display vs storage).
    """
    return redact(text)