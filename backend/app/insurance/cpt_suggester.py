"""Patient-context CPT decision support constrained to an uploaded rate CSV."""

from __future__ import annotations

import json
import re
from typing import Any

import boto3

from app.ingestion.bedrock_extractor import BEDROCK_MODEL_ID


_SYSTEM = """You provide clinical coding decision support for a clinician. Given patient-supplied
clinical context and the exact CPT/HCPCS codes in that clinic's uploaded rate CSV, recommend only
plausible candidate codes from that list. Do not invent a code, diagnosis, service, unit, or fact.
These are suggestions only; the clinician must choose the final code after the encounter.
Return JSON only: {\"suggestions\":[{\"code\":\"...\",\"reason\":\"brief explanation tied to the supplied context\"}]}.
Return an empty suggestions list when the context does not support a candidate."""


def suggest_cpt_codes(context: str, rate_lines: list[dict[str, Any]]) -> dict[str, str]:
    """Return model suggestions, accepting only codes supplied in the uploaded CSV."""
    candidates = [
        {"code": str(line["code"]), "description": str(line.get("description") or line["code"])}
        for line in rate_lines
    ]
    if not candidates:
        return {}
    response = boto3.client("bedrock-runtime").converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": _SYSTEM}],
        messages=[{"role": "user", "content": [{"text": json.dumps({"clinical_context": context, "available_codes": candidates})}]}],
        inferenceConfig={"maxTokens": 800, "temperature": 0},
    )
    blocks = response.get("output", {}).get("message", {}).get("content", [])
    text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict)).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("CPT suggestion service returned an invalid response")
    payload = json.loads(match.group(0))
    allowed = {line["code"] for line in candidates}
    suggestions: dict[str, str] = {}
    for item in payload.get("suggestions", []):
        if not isinstance(item, dict):
            continue
        code, reason = str(item.get("code", "")).upper(), str(item.get("reason", "")).strip()
        if code in allowed and reason and code not in suggestions:
            suggestions[code] = reason[:500]
    return suggestions
