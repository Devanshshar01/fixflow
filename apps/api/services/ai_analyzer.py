"""
AI analysis layer — swappable interface for Gemini / Ollama.

V1: Gemini 2.5 Flash (free tier)
V2: Ollama (local, zero API cost) — swap via AI_PROVIDER env var

Never receives raw logs. Only receives:
- workflow name
- failed step name
- detected ecosystem
- redacted 100-line snippet
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import google.generativeai as genai
from pydantic import BaseModel, field_validator

from config import get_settings
from logger import logger


# ── Response schema ───────────────────────────────────────────────────────────

VALID_CATEGORIES = {"node", "docker", "python", "testing", "permissions", "secrets", "other"}


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


def _normalize_analysis_payload(value: Any, *, context: str) -> dict:
    logger.info(
        "Normalizing AI analysis payload",
        context=context,
        object_type=type(value).__name__,
        object_preview=_preview(value),
    )

    if isinstance(value, dict):
        return value

    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, dict):
                logger.warning(
                    "AI analysis payload was a list; using first dict item",
                    context=context,
                    selected_index=index,
                    object_preview=_preview(item),
                )
                return item

        raise AIAnalysisError(
            f"{context} returned a list without a JSON object: {_preview(value)}"
        )

    raise AIAnalysisError(
        f"{context} returned {type(value).__name__}, expected dict: {_preview(value)}"
    )


class AIAnalysis(BaseModel):
    root_cause: str
    confidence: int
    fix: str
    prevention: str
    category: str

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, v: int) -> int:
        return max(0, min(100, v))

    @field_validator("category")
    @classmethod
    def valid_category(cls, v: str) -> str:
        return v if v in VALID_CATEGORIES else "other"

    @field_validator("root_cause", "fix", "prevention")
    @classmethod
    def non_empty(cls, v: str) -> str:
        return v.strip() or "Unable to determine"


# ── Analysis context ──────────────────────────────────────────────────────────

@dataclass
class AnalysisContext:
    workflow_name: str
    failed_step: str
    ecosystem: str
    redacted_snippet: str


# ── Abstract base ─────────────────────────────────────────────────────────────

class AIAnalyzer(ABC):
    @abstractmethod
    async def analyze(self, ctx: AnalysisContext) -> AIAnalysis:
        ...


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(ctx: AnalysisContext) -> str:
    return f"""You are a senior DevOps engineer with expertise in CI/CD debugging.
Respond ONLY with valid JSON — no markdown, no preamble, no explanation outside the JSON object.

Workflow: {ctx.workflow_name}
Failed step: {ctx.failed_step}
Environment: {ctx.ecosystem}

Error log (relevant section only, secrets already redacted):
{ctx.redacted_snippet}

Respond with this exact JSON structure:
{{
  "root_cause": "one clear sentence explaining the root cause",
  "confidence": 85,
  "fix": "exact actionable steps to fix this",
  "prevention": "how to prevent this failure in future",
  "category": "node|docker|python|testing|permissions|secrets|other"
}}"""


# ── Gemini implementation ─────────────────────────────────────────────────────

class GeminiAnalyzer(AIAnalyzer):
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            generation_config=genai.GenerationConfig(
                temperature=0.1,        # Low temp for consistent structured output
                max_output_tokens=1024,
            ),
        )
        logger.info("GeminiAnalyzer initialized", model="gemini-2.0-flash")

    async def analyze(self, ctx: AnalysisContext) -> AIAnalysis:
        import asyncio
        import json
        import random

        prompt = _build_prompt(ctx)
        max_retries = 3

        for attempt in range(max_retries):
            try:
                # Gemini SDK is sync — run in thread pool to not block event loop
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self._model.generate_content(prompt),
                )

                raw = response.text.strip()

                # Strip markdown code fences if model wraps response
                if raw.startswith("```"):
                    raw = re.sub(r"^```(?:json)?\n?", "", raw)
                    raw = re.sub(r"\n?```$", "", raw)

                data = _normalize_analysis_payload(
                    json.loads(raw),
                    context="gemini_response",
                )
                analysis = AIAnalysis(**data)

                logger.info(
                    "Gemini analysis complete",
                    confidence=analysis.confidence,
                    category=analysis.category,
                )
                return analysis

            except Exception as exc:
                error_str = str(exc).lower()
                is_rate_limit = "429" in error_str or "quota" in error_str or "rate" in error_str

                if attempt == max_retries - 1:
                    logger.error(
                        "Gemini analysis failed after retries",
                        error=str(exc),
                        attempts=max_retries,
                    )
                    raise AIAnalysisError(f"Gemini failed: {exc}") from exc

                wait = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    "Gemini call failed — retrying",
                    attempt=attempt + 1,
                    wait_seconds=round(wait, 1),
                    is_rate_limit=is_rate_limit,
                    error=str(exc),
                )
                await asyncio.sleep(wait)

        raise AIAnalysisError("Gemini analysis exhausted retries")


# ── Ollama implementation (V2 — config swap) ──────────────────────────────────

class OllamaAnalyzer(AIAnalyzer):
    def __init__(self, base_url: str, model: str = "qwen3:4b"):
        self._base_url = base_url.rstrip("/")
        self._model = model
        logger.info("OllamaAnalyzer initialized", model=model, url=base_url)

    async def analyze(self, ctx: AnalysisContext) -> AIAnalysis:
        import json
        import httpx

        prompt = _build_prompt(ctx)

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
            )

        if response.status_code != 200:
            raise AIAnalysisError(f"Ollama returned {response.status_code}")

        response_json = response.json()
        raw = _safe_get(response_json, "response", "", context="ollama_response")
        data = _normalize_analysis_payload(
            json.loads(raw),
            context="ollama_response.response",
        )
        return AIAnalysis(**data)


# ── Factory ───────────────────────────────────────────────────────────────────

def get_analyzer() -> AIAnalyzer:
    settings = get_settings()
    if settings.ai_provider == "ollama":
        return OllamaAnalyzer(base_url=settings.ollama_url)
    return GeminiAnalyzer(api_key=settings.gemini_api_key)


# ── Exceptions ────────────────────────────────────────────────────────────────

class AIAnalysisError(Exception):
    pass


# ── Fix import missing from ai_analyzer ───────────────────────────────────────
import re  # noqa: E402 — needed by GeminiAnalyzer, placed after class def for readability
