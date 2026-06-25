"""
Multi-step log parser for GitHub Actions workflow run ZIPs.

Strategy:
1. Unzip the log archive — one .txt file per job/step
2. Scan every step for failure markers
3. Identify the ROOT failing step (first failure; rest are cascades)
4. Extract a 100-line window around the error in the root step
5. Detect the ecosystem (node/python/docker/rust/go/unknown)
6. Apply size guards — never process a step file >2MB
"""

import zipfile
import io
import re
from dataclasses import dataclass, field

from config import get_settings
from logger import logger


# ── Failure detection ─────────────────────────────────────────────────────────

FAILURE_MARKERS = [
    r"##\[error\]",
    r"Error:",
    r"error:",
    r"FAILED",
    r"FAILURE",
    r"Process completed with exit code [^0]",
    r"npm ERR!",
    r"ModuleNotFoundError",
    r"ImportError",
    r"SyntaxError",
    r"TypeError",
    r"RuntimeError",
    r"AssertionError",
    r"fatal:",
    r"FATAL",
]

FAILURE_PATTERN = re.compile(
    "|".join(FAILURE_MARKERS),
    re.IGNORECASE,
)

# ── Ecosystem detection ───────────────────────────────────────────────────────

ECOSYSTEM_SIGNALS: dict[str, list[str]] = {
    "node": ["npm", "yarn", "pnpm", "node", "jest", "webpack", "vite", "eslint", "tsc", "typescript"],
    "python": ["pip", "pytest", "poetry", "tox", "mypy", "ruff", "python", "django", "fastapi"],
    "docker": ["docker", "dockerfile", "container", "registry", "image", "push", "pull"],
    "rust": ["cargo", "rustc", "clippy", "rustfmt"],
    "go": ["go build", "go test", "golangci", "go mod"],
    "java": ["maven", "gradle", "mvn", "javac"],
    "ruby": ["bundle", "gem", "rspec", "rails"],
}


def detect_ecosystem(step_name: str, log_snippet: str) -> str:
    combined = (step_name + " " + log_snippet[:1000]).lower()
    for lang, signals in ECOSYSTEM_SIGNALS.items():
        if any(s in combined for s in signals):
            return lang
    return "unknown"


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class StepLog:
    name: str
    content: str
    size_bytes: int
    truncated: bool = False
    has_failure: bool = False
    failure_line_index: int = -1


@dataclass
class ParsedLogs:
    root_step: StepLog | None
    cascading_steps: list[str]
    all_steps: list[StepLog]
    ecosystem: str
    snippet: str
    total_steps: int
    total_failing_steps: int
    size_guard_triggered: bool = False


# ── Core parser ───────────────────────────────────────────────────────────────

def parse_log_zip(zip_bytes: bytes) -> ParsedLogs:
    """
    Full pipeline: unzip → parse steps → detect failures → extract context.
    """
    settings = get_settings()
    size_guard_triggered = False

    # ── 1. Unzip ───────────────────────────────────────────────────────────────
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        logger.error("Log ZIP is corrupt or not a valid ZIP file")
        raise ValueError("Invalid log ZIP")

    step_files = sorted(
        [f for f in zf.namelist() if f.endswith(".txt")],
        key=lambda x: x,
    )

    logger.info("Parsing log ZIP", total_files=len(step_files))

    # ── 2. Parse each step file ────────────────────────────────────────────────
    steps: list[StepLog] = []

    for filename in step_files:
        info = zf.getinfo(filename)
        size = info.file_size

        # Friendly step name: strip numbering prefix and .txt suffix
        step_name = _clean_step_name(filename)

        # Size guard — skip or truncate oversized step logs
        if size > settings.max_log_bytes_per_step:
            size_guard_triggered = True
            logger.warning(
                "Step log exceeds size limit — truncating",
                step=step_name,
                size_bytes=size,
                limit_bytes=settings.max_log_bytes_per_step,
            )
            raw = zf.read(filename)
            content = _truncate_large_log(raw.decode("utf-8", errors="replace"))
            truncated = True
        else:
            raw = zf.read(filename)
            content = raw.decode("utf-8", errors="replace")
            truncated = False

        # Detect failure
        has_failure, failure_line_index = _find_failure_line(content)

        step = StepLog(
            name=step_name,
            content=content,
            size_bytes=size,
            truncated=truncated,
            has_failure=has_failure,
            failure_line_index=failure_line_index,
        )
        steps.append(step)

    # ── 3. Identify root vs cascading failures ─────────────────────────────────
    failing_steps = [s for s in steps if s.has_failure]

    if not failing_steps:
        logger.warning("No clear failure markers found in any step log")
        # Fall back: use the last step's tail as the snippet
        last_step = steps[-1] if steps else None
        return ParsedLogs(
            root_step=last_step,
            cascading_steps=[],
            all_steps=steps,
            ecosystem="unknown",
            snippet=_tail_lines(last_step.content, 80) if last_step else "",
            total_steps=len(steps),
            total_failing_steps=0,
            size_guard_triggered=size_guard_triggered,
        )

    # First failure = root cause; rest = cascading
    root_step = failing_steps[0]
    cascading = [s.name for s in failing_steps[1:]]

    # ── 4. Extract context snippet ─────────────────────────────────────────────
    snippet = _extract_around_failure(root_step)

    # ── 5. Detect ecosystem ────────────────────────────────────────────────────
    ecosystem = detect_ecosystem(root_step.name, snippet)

    logger.info(
        "Log parsing complete",
        total_steps=len(steps),
        failing_steps=len(failing_steps),
        root_step=root_step.name,
        cascading_steps=cascading,
        ecosystem=ecosystem,
        snippet_lines=len(snippet.splitlines()),
        size_guard_triggered=size_guard_triggered,
    )

    return ParsedLogs(
        root_step=root_step,
        cascading_steps=cascading,
        all_steps=steps,
        ecosystem=ecosystem,
        snippet=snippet,
        total_steps=len(steps),
        total_failing_steps=len(failing_steps),
        size_guard_triggered=size_guard_triggered,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean_step_name(filename: str) -> str:
    """
    Convert '1_Set up job.txt' or 'job1/1_Set up job.txt' to 'Set up job'.
    """
    name = filename.split("/")[-1]          # strip job folder prefix
    name = re.sub(r"^\d+_", "", name)       # strip numeric prefix
    name = name.replace(".txt", "")         # strip extension
    return name.strip()


def _find_failure_line(content: str) -> tuple[bool, int]:
    """
    Scan for the first failure marker. Returns (found, line_index).
    """
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if FAILURE_PATTERN.search(line):
            return True, i
    return False, -1


def _extract_around_failure(step: StepLog, window: int = 100) -> str:
    """
    Extract `window` lines centered on the first failure marker.
    Gives 40% before the error and 60% after — the fix is usually below the error.
    """
    lines = step.content.splitlines()
    failure_idx = step.failure_line_index

    if failure_idx == -1:
        # No specific failure line — return the tail
        return _tail_lines(step.content, window)

    before = int(window * 0.4)
    after = int(window * 0.6)

    start = max(0, failure_idx - before)
    end = min(len(lines), failure_idx + after)

    extracted = lines[start:end]

    # Prepend a marker so AI/rules know where the error line is
    relative_error_line = failure_idx - start
    if 0 <= relative_error_line < len(extracted):
        extracted[relative_error_line] = ">>> " + extracted[relative_error_line]

    return "\n".join(extracted)


def _tail_lines(content: str, n: int) -> str:
    lines = content.splitlines()
    return "\n".join(lines[-n:])


def _truncate_large_log(content: str, head_lines: int = 500, tail_lines: int = 500) -> str:
    """
    For oversized logs: keep first 500 + last 500 lines with a notice in between.
    """
    lines = content.splitlines()
    if len(lines) <= head_lines + tail_lines:
        return content

    head = lines[:head_lines]
    tail = lines[-tail_lines:]
    notice = [
        "",
        f"... [{len(lines) - head_lines - tail_lines} lines omitted — log exceeded size limit] ...",
        "",
    ]
    return "\n".join(head + notice + tail)