"""LLM-assisted repair (S5) — src/validation/.

Used only for messy categorical / free-text fields the deterministic synonym
tables could not resolve (``level``, ``severity``, and any enum field that
survived standardization).  Every attempted call is counted toward the §6
stderr metrics (``llm_calls`` / ``llm_tokens``) so pipeline-health monitoring
sees real usage; any failure — missing key, network error, unparseable or
non-enum reply — falls back to the deterministic path.  With ``--no-llm``
this module is never invoked and tests run fully offline.

The field value comes from untrusted source data; the prompt instructs the
model to reply with a single JSON object and every reply is validated
against the target enum before it is accepted.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def repair_enums_with_llm(
    unresolved: dict[str, str],
    allowed: dict[str, list[str]],
) -> tuple[dict[str, str], int, int]:
    """Ask the LLM to map messy categorical values onto their §5 enums.

    Args:
        unresolved: ``{field: messy_value}`` for fields not yet enum-valid.
        allowed: ``{field: [enum members]}`` from the rule catalog.

    Returns:
        ``(repairs, llm_calls, llm_tokens)`` where ``repairs`` maps a field to
        an accepted enum member.  ``llm_calls`` counts actual API attempts
        (including failed ones); ``llm_tokens`` is the reported usage of
        successful calls.
    """
    if not unresolved:
        return {}, 0, 0
    if os.environ.get("ANTHROPIC_API_KEY") is None:
        return {}, 0, 0
    try:
        import anthropic  # declared in src/validation/requirements.txt
    except ImportError:
        return {}, 0, 0

    calls = 0
    tokens = 0
    try:
        client = anthropic.Anthropic()
        prompt_lines = [
            "You classify EV-charging data field values into fixed enums.",
            "Reply with ONLY a JSON object mapping each field name to exactly one",
            "of its allowed values. Do not explain.",
            "",
        ]
        for field, value in unresolved.items():
            prompt_lines.append(f"{field}: value={value!r} allowed={allowed.get(field, [])}")
        response = client.messages.create(
            model=_MODEL,
            max_tokens=128,
            messages=[{"role": "user", "content": "\n".join(prompt_lines)}],
        )
        calls = 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            tokens = int(getattr(usage, "input_tokens", 0) or 0) + int(
                getattr(usage, "output_tokens", 0) or 0
            )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        parsed = _extract_json(text) or {}
        repairs: dict[str, str] = {}
        for field, value in parsed.items():
            if field in allowed and str(value) in allowed[field]:
                repairs[field] = str(value)
        return repairs, calls, tokens
    except Exception:
        # A call was attempted; count it, report no repairs, no tokens.
        return {}, calls, tokens
