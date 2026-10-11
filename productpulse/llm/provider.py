"""Optional LLM theme summaries behind a provider abstraction.

Guarantees
- Core features never depend on this module; it is off unless
  PRODUCTPULSE_LLM_PROVIDER and PRODUCTPULSE_LLM_API_KEY are set.
- The model only sees the feedback records of one theme (IDs + text).
- Output must be JSON matching ``LLMSummary``; every cited ID must belong to
  the supplied evidence, and any quoted passage must appear verbatim in it.
- Any failure (timeout, HTTP error, refusal, invalid output) falls back to the
  deterministic summary. Error messages never include the API key.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from productpulse.config import Settings
from productpulse.logging_setup import get_logger

log = get_logger("llm")

MAX_EVIDENCE_ITEMS = 40
MAX_EVIDENCE_CHARS = 600
DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "openai": "gpt-4o-mini"}

SYSTEM_PROMPT = (
    "You summarise customer feedback for a product team. Use ONLY the feedback records provided. "
    "Do not invent facts, numbers, customers or quotes. If you quote, copy the words exactly from a record. "
    'Respond with a single JSON object and nothing else: {"summary": "<2-4 sentences describing the '
    'customer problem>", "cited_ids": ["<IDs of the records that support the summary>"]}.'
)


class LLMSummary(BaseModel):
    summary: str = Field(min_length=20, max_length=1500)
    cited_ids: list[str] = Field(min_length=1, max_length=40)


class LLMValidationError(ValueError):
    pass


@dataclass
class SummaryOutcome:
    summary: str
    cited_ids: list[str]
    provider: str  # e.g. "anthropic:claude-opus-5-5" or "deterministic"
    used_fallback: bool
    reason: str = ""


Caller = Callable[[Settings, str, str], str]  # (settings, system, user) -> raw text


def build_user_prompt(theme_label: str, evidence: list[tuple[str, str]]) -> str:
    lines = [f"Theme label (machine-generated): {theme_label}", "", "Feedback records:"]
    for fid, text in evidence[:MAX_EVIDENCE_ITEMS]:
        lines.append(json.dumps({"id": fid, "text": text[:MAX_EVIDENCE_CHARS]}, ensure_ascii=False))
    return "\n".join(lines)


_QUOTE = re.compile(r"[\"“”]([^\"“”]{12,})[\"“”]")


def validate_output(raw: str, evidence: list[tuple[str, str]]) -> LLMSummary:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMValidationError("response was not a JSON object")
    try:
        parsed = LLMSummary.model_validate(json.loads(text[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LLMValidationError(f"response did not match the schema: {str(exc)[:200]}") from exc

    allowed = {fid for fid, _ in evidence}
    unknown = [c for c in parsed.cited_ids if c not in allowed]
    if unknown:
        raise LLMValidationError(f"cited IDs not in the supplied evidence: {unknown[:5]}")
    corpus = [t.lower() for _, t in evidence]
    for quote in _QUOTE.findall(parsed.summary):
        q = re.sub(r"\s+", " ", quote).strip().lower()
        if not any(q in re.sub(r"\s+", " ", c) for c in corpus):
            raise LLMValidationError("summary contains a quotation that does not appear in the evidence")
    parsed.cited_ids = list(dict.fromkeys(parsed.cited_ids))
    return parsed


def _call_anthropic(settings: Settings, system: str, user: str) -> str:
    import anthropic

    kwargs = {"api_key": settings.llm_api_key, "timeout": settings.llm_timeout_seconds, "max_retries": 1}
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url
    client = anthropic.Anthropic(**kwargs)
    response = client.beta.messages.create(
        model=settings.llm_model or DEFAULT_MODELS["anthropic"],
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined the request")
    return "".join(b.text for b in response.content if b.type == "text")


def _call_openai_compatible(settings: Settings, system: str, user: str) -> str:
    import httpx

    base = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
    resp = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        json={
            "model": settings.llm_model or DEFAULT_MODELS["openai"],
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        timeout=settings.llm_timeout_seconds,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


CALLERS: dict[str, Caller] = {"anthropic": _call_anthropic, "openai": _call_openai_compatible}


def _safe_error(exc: Exception, settings: Settings) -> str:
    msg = f"{type(exc).__name__}: {exc}"[:300]
    if settings.llm_api_key:
        msg = msg.replace(settings.llm_api_key, "***")
    return msg


def summarize_theme(
    settings: Settings,
    theme_label: str,
    evidence: list[tuple[str, str]],
    deterministic_summary: str,
    caller: Caller | None = None,
) -> SummaryOutcome:
    fallback_ids = [fid for fid, _ in evidence[:5]]
    if not evidence:
        return SummaryOutcome(deterministic_summary, [], "deterministic", True, "no evidence records")
    if not settings.llm_enabled and caller is None:
        return SummaryOutcome(deterministic_summary, fallback_ids, "deterministic", True, "LLM provider not configured")

    provider = settings.llm_provider
    model = settings.llm_model or DEFAULT_MODELS.get(provider, "")
    call = caller or CALLERS[provider]
    try:
        raw = call(settings, SYSTEM_PROMPT, build_user_prompt(theme_label, evidence))
        parsed = validate_output(raw, evidence[:MAX_EVIDENCE_ITEMS])
    except Exception as exc:  # network, timeout, HTTP, refusal, validation — all fall back
        reason = _safe_error(exc, settings)
        log.warning("llm summary fallback", extra={"ctx": {"provider": provider, "reason": reason}})
        return SummaryOutcome(deterministic_summary, fallback_ids, "deterministic", True, reason)
    return SummaryOutcome(parsed.summary, parsed.cited_ids, f"{provider}:{model}", False)
