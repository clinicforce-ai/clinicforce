"""Source-linked product exclusions from executed Clinical Library agreements."""
from __future__ import annotations

import re

from app.core import staff_store
from app.insurance import commercial


_MA_PPO = re.compile(r"\b(?:regional|local)\s+(?:medicare\s+advantage\s+)?ppo\b|\b(?:regional|local)\s+ppo\s+medicare\s+advantage\b", re.I)
_EXCLUDES = re.compile(r"\b(?:exclud(?:e|es|ed|ing)|not\s+included|does\s+not\s+include|not\s+applicable|except)\b", re.I)


def product_kind(name: str) -> str | None:
    value = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
    if not re.search(r"\b(?:medicare|advantage|complete care)\b", value):
        return None
    if re.search(r"\b(?:regional (?:medicare advantage )?ppo|rppo)\b", value):
        return "regional_medicare_advantage_ppo"
    if re.search(r"\b(?:local (?:medicare advantage )?ppo|lppo)\b", value):
        return "local_medicare_advantage_ppo"
    return None


def compile_exclusions(document: dict, extracted: dict) -> list[dict]:
    """Only explicit exclusion clauses become rules; keep the entire source passage."""
    if document.get("kind") != "contract" or not commercial.is_united_healthcare_payer(
        {"payer": document.get("payer") or ""}
    ):
        return []
    records = []
    for page in extracted.get("pages") or []:
        text = str(page.get("text") or "")
        for passage in re.split(r"\n\s*\n", text):
            for match in _MA_PPO.finditer(passage):
                nearby = passage[max(0, match.start() - 250):match.end() + 250]
                if (not _EXCLUDES.search(nearby) or not re.search(r'\bmedicare\s+advantage\b', nearby, re.I)
                        or re.search(r"\b(?:does\s+not|do\s+not|not)\s+(?:specifically\s+)?exclud(?:e|es|ed|ing)\b", nearby, re.I)):
                    continue
                label = re.search(r"\b(regional|local)\b", match.group(), re.I)
                if not label:
                    continue
                kind = label.group(1).lower() + "_medicare_advantage_ppo"
                if any(row['product_kind'] == kind and row['page'] == page['number'] for row in records):
                    continue
                records.append({"product_kind": kind, "decision": "excluded_under_available_agreement",
                                "document_id": document["id"], "document_sha256": extracted["sha256"],
                                "uploaded_by": document.get("created_by"),
                                "uploaded_at": document.get("created_at"),
                                "page": page["number"], "locator": f"Page {page['number']}",
                                "passage": nearby.strip(), "effective_from": document.get("effective_from"),
                                "effective_to": document.get("effective_to")})
    return records


def applicable_exclusion(practice_id: str, payer: str, products: list[str], service_date: str,
                         *, documents: list[dict] | None = None) -> dict | None:
    """An exclusion limits its own agreement; it does not establish out-of-network status."""
    if not commercial.is_united_healthcare_payer({"payer": payer}):
        return None
    kinds = {kind for product in products if (kind := product_kind(str(product)))}
    if len(kinds) != 1 or not service_date:
        return None
    matches = []
    for metadata in documents if documents is not None else staff_store.list_library(practice_id):
        if metadata.get("kind") != "contract":
            continue
        document = metadata if documents is not None else staff_store.get_library(metadata["id"], practice_id)
        if not document or not commercial.is_united_healthcare_payer({"payer": document.get("payer") or ""}):
            continue
        extracted = document.get("extraction") or {}
        compiled = extracted.get("compiled") or {}
        for row in compiled.get("exclusions") or []:
            if (row.get("product_kind") in kinds and row.get("document_sha256") == document.get("sha256")
                    and (not row.get("effective_from") or row["effective_from"] <= service_date)
                    and (not row.get("effective_to") or service_date <= row["effective_to"])):
                matches.append({**row, "document_label": document.get("label")})
    return min(matches, key=lambda row: (row["document_id"], row["page"])) if matches else None
