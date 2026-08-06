"""P4 — Reporting & Experience: plain-language summaries (M4).

LLM path uses the Claude API via ``ANTHROPIC_API_KEY`` from the environment
(never committed). The ``--no-llm`` path is a fully deterministic template
fallback so every test runs offline.

Design: summaries are *of* numbers computed by earlier stages — the LLM is
asked to paraphrase fleet figures, never to compute them. Both paths return
the same shape: a list of {"title", "text", "llm_used"} dicts. Any LLM
failure (no key, network, API error) falls back to templates and is counted
honestly in the metrics line (llm_calls reflects actual attempts).
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .kpis import CHRONIC_FAILURE_THRESHOLD, Kpis
from .render import fmt_number, fmt_pct


def generate_summaries(kpis: Kpis, no_llm: bool = False) -> List[Dict]:
    """Return (title, text, llm_used) summaries for the CX view (M4)."""
    templates = _template_summaries(kpis)
    if no_llm:
        return [dict(t, llm_used=False) for t in templates]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return [dict(t, llm_used=False) for t in templates]

    try:
        text = _llm_paraphrase(kpis, api_key)
        return [{"title": "Fleet summary", "text": text, "llm_used": True}]
    except Exception:
        # Any failure → deterministic fallback; never crash the report.
        return [dict(t, llm_used=False) for t in templates]


def _template_summaries(kpis: Kpis) -> List[Dict]:
    k = kpis.as_dict()
    uptime = fmt_pct(k["est_uptime_pct"])
    return [
        {
            "title": "Fleet summary",
            "text": (
                f"The fleet has {k['chargers_deployed']} chargers ({k['active_chargers']} active) "
                f"across {len(kpis.health_states)} health states. Estimated fleet uptime is {uptime}; "
                f"{k['sessions_count']} sessions delivered {k['energy_delivered_kwh']} kWh. "
                f"Data completeness (mean quality score) is {fmt_pct(k['data_completeness_pct'])}."
            ),
        },
        {
            "title": "Safety summary",
            "text": (
                f"{k['anomalies_total']} anomalies were flagged, of which "
                f"{kpis.anomaly_severity.get('SAFETY', 0)} are SAFETY-level. "
                f"{len(kpis.chronic_failure_sites)} site(s) show chronic failure "
                f"(fault recurrence ≥ {CHRONIC_FAILURE_THRESHOLD})."
            ),
        },
        {
            "title": "Reliability summary",
            "text": (
                f"{kpis.health_states.get('HEALTHY', 0)} chargers are healthy, "
                f"{kpis.health_states.get('DEGRADED', 0)} degraded, "
                f"{kpis.health_states.get('SUSPECT_OUTAGE', 0)} suspected of outage, and "
                f"{kpis.health_states.get('SAFETY_REVIEW', 0)} under safety review."
            ),
        },
    ]


def _llm_paraphrase(kpis: Kpis, api_key: str) -> str:
    """Call Claude to paraphrase fleet figures; raises on any failure."""
    import anthropic  # lazy import — only needed on the LLM path

    k = kpis.as_dict()
    prompt = (
        "You are writing a short, plain-language operations summary for EV charging "
        "customers. Paraphrase ONLY the figures given; do not invent or compute "
        "anything. Two or three sentences, no markdown.\n\n"
        f"Chargers deployed: {k['chargers_deployed']} (active: {k['active_chargers']})\n"
        f"Sessions: {k['sessions_count']}, energy kWh: {k['energy_delivered_kwh']}\n"
        f"Fleet est. uptime %: {k['est_uptime_pct']}\n"
        f"Data completeness %: {k['data_completeness_pct']}\n"
        f"Anomalies: {k['anomalies_total']} (SAFETY: {kpis.anomaly_severity.get('SAFETY', 0)})\n"
        f"Chronic-failure sites: {len(kpis.chronic_failure_sites)}"
    )
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=300,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in message.content if getattr(block, "type", "") == "text").strip()
